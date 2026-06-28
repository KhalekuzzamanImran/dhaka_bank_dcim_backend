import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from celery.exceptions import SoftTimeLimitExceeded
from django.db import models
from django.utils import timezone

try:
    from pymodbus.client import ModbusTcpClient
except Exception:  # pragma: no cover
    ModbusTcpClient = None

from apps.alerts.services import evaluate_latest
from collectors.common.locks import acquire_device_poll_lock, release_device_poll_lock, acquire_modbus_gateway_lock, release_modbus_gateway_lock
from collectors.common.telemetry_writer import write_device_telemetry_bulk
from collectors.common.value_converter import normalize_value
from apps.devices.models import Device, DevicePollingConfig, DeviceProtocolConfig, DeviceStatus, ModbusRegisterMapping, ProtocolType
from apps.telemetry.models import LatestTelemetry, TelemetryIngestLog, TelemetryQuality
from .exceptions import ModbusConfigurationError, ModbusResponseError

logger = logging.getLogger(__name__)


@dataclass
class ModbusPollOutcome:
    device_id: str
    ingest_id: str
    status: str
    success_count: int
    failure_count: int
    error_message: Optional[str] = None


def get_enabled_modbus_devices_due(limit=100):
    now = timezone.now()
    qs = (
        DevicePollingConfig.objects.select_related("device", "polling_profile")
        .filter(
            is_enabled=True,
            polling_profile__is_active=True,
            polling_profile__protocol=ProtocolType.MODBUS_TCP,
            device__is_active=True,
            next_poll_at__lte=now,
        )
        .order_by("next_poll_at")
    )
    return [str(x.device_id) for x in qs[:limit]]


def _get_runtime(device):
    protocol_config = (
        DeviceProtocolConfig.objects.filter(device=device, protocol=ProtocolType.MODBUS_TCP, is_enabled=True)
        .order_by("-is_primary", "created_at")
        .first()
    )
    if not protocol_config:
        raise ModbusConfigurationError("Modbus TCP protocol config is missing or disabled")
    mappings = list(
        (
            ModbusRegisterMapping.objects.select_related("metric")
            .filter(device_type=device.device_type, is_active=True)
            .filter(models.Q(device_model=device.device_model) | models.Q(device_model__isnull=True))
        )
    )
    deduped = {}
    for mapping in mappings:
        current = deduped.get(mapping.metric_id)
        if current is None or mapping.device_model_id:
            deduped[mapping.metric_id] = mapping
    mappings = list(deduped.values())
    if not mappings:
        raise ModbusConfigurationError("No active Modbus register mappings found for this device type/model")
    return protocol_config, mappings


def _read_mapping(client, mapping):
    kwargs = {"address": mapping.register_address, "count": mapping.register_count, "slave": mapping.slave_id}
    if mapping.function_code == 1:
        result = client.read_coils(**kwargs)
        if result.isError():
            raise ModbusResponseError(str(result))
        return bool(result.bits[0])
    if mapping.function_code == 2:
        result = client.read_discrete_inputs(**kwargs)
        if result.isError():
            raise ModbusResponseError(str(result))
        return bool(result.bits[0])
    if mapping.function_code == 3:
        result = client.read_holding_registers(**kwargs)
    elif mapping.function_code == 4:
        result = client.read_input_registers(**kwargs)
    else:
        raise ModbusResponseError(f"Unsupported Modbus function code: {mapping.function_code}")
    if result.isError():
        raise ModbusResponseError(str(result))
    registers = result.registers
    data_type = (mapping.data_type or "uint16").lower()
    if data_type in {"uint16", "integer", "int"}:
        return int(registers[0])
    if data_type == "int16":
        value = registers[0]
        return value - 65536 if value > 32767 else value
    if data_type in {"uint32", "int32", "float32"}:
        if len(registers) < 2:
            raise ModbusResponseError("Expected 2 registers")
        raw = (registers[0] << 16) + registers[1]
        if data_type == "int32" and raw > 2147483647:
            raw -= 4294967296
        if data_type == "float32":
            import struct
            return struct.unpack(">f", raw.to_bytes(4, "big"))[0]
        return raw
    return registers[0]


def _mark_success(device, polling_config, at):
    Device.objects.filter(pk=device.pk).update(status=DeviceStatus.ONLINE, last_seen_at=at)
    if polling_config:
        interval = polling_config.polling_profile.interval_seconds
        DevicePollingConfig.objects.filter(pk=polling_config.pk).update(
            last_polled_at=at,
            next_poll_at=at + timezone.timedelta(seconds=interval),
            consecutive_failures=0,
            last_error_message="",
        )


def _mark_failure(device, polling_config, error_message, at):
    failure_count = (polling_config.consecutive_failures + 1) if polling_config else 1
    status = DeviceStatus.DEGRADED if failure_count < 3 else DeviceStatus.OFFLINE
    Device.objects.filter(pk=device.pk).update(status=status)
    if polling_config:
        interval = min(polling_config.polling_profile.interval_seconds, 300)
        DevicePollingConfig.objects.filter(pk=polling_config.pk).update(
            last_polled_at=at,
            next_poll_at=at + timezone.timedelta(seconds=interval),
            consecutive_failures=failure_count,
            last_error_message=str(error_message)[:2000],
        )


def poll_modbus_device(device_id: str, evaluate_alerts=True) -> ModbusPollOutcome:
    if not acquire_device_poll_lock(device_id, timeout=120):
        return ModbusPollOutcome(str(device_id), "", "SKIPPED", 0, 0, "Device already being polled")
    started_at = timezone.now()
    ingest_id = uuid.uuid4()
    success_count = 0
    failure_count = 0
    error_message = None
    device = Device.objects.select_related("organization", "data_center", "device_type", "device_model").get(pk=device_id)
    polling_config = getattr(device, "polling_config", None)
    client = None
    gateway_locked = False
    try:
        if ModbusTcpClient is None:
            raise ModbusConfigurationError("pymodbus is not installed or failed to import")
        protocol_config, mappings = _get_runtime(device)
        host = protocol_config.host
        port = protocol_config.port or 502
        # Protect TCP-to-RTU gateways. Direct TCP devices also work with this conservative lock.
        gateway_locked = acquire_modbus_gateway_lock(host, port, timeout=max(protocol_config.timeout_seconds * 4, 20))
        if not gateway_locked:
            return ModbusPollOutcome(str(device.pk), str(ingest_id), "SKIPPED", 0, 0, "Modbus gateway busy")
        client = ModbusTcpClient(host=host, port=port, timeout=protocol_config.timeout_seconds)
        if not client.connect():
            raise ModbusResponseError(f"Could not connect to Modbus TCP device {host}:{port}")
        readings = []
        for mapping in mappings:
            try:
                raw = _read_mapping(client, mapping)
                value = normalize_value(raw, mapping.data_type, mapping.scale_factor, mapping.offset_value)
                readings.append({"metric": mapping.metric, "value": value, "quality": TelemetryQuality.GOOD})
                success_count += 1
            except Exception as exc:
                failure_count += 1
                logger.warning("Modbus mapping failed device=%s register=%s error=%s", device.pk, mapping.register_address, exc)
        if success_count == 0:
            raise ModbusResponseError("All configured Modbus registers failed")
        write_device_telemetry_bulk(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            readings=readings,
            source="modbus_worker",
            ingest_id=ingest_id,
            timestamp=started_at,
        )
        if evaluate_alerts:
            for latest in LatestTelemetry.objects.filter(device=device, metric__in=[r["metric"] for r in readings]):
                evaluate_latest(latest)
        _mark_success(device, polling_config, started_at)
        status = "SUCCESS" if failure_count == 0 else "PARTIAL_SUCCESS"
    except SoftTimeLimitExceeded:
        error_message = "Modbus poll exceeded worker soft time limit"
        _mark_failure(device, polling_config, error_message, timezone.now())
        status = "FAILED"
    except Exception as exc:
        error_message = str(exc)
        _mark_failure(device, polling_config, error_message, timezone.now())
        status = "FAILED"
        logger.exception("Modbus poll failed device=%s", device.pk)
    finally:
        if client:
            client.close()
        if gateway_locked:
            try:
                release_modbus_gateway_lock(host, port)
            except Exception:
                pass
        release_device_poll_lock(device_id)
    finished_at = timezone.now()
    TelemetryIngestLog.objects.create(
        ingest_id=ingest_id,
        device=device,
        protocol="MODBUS_TCP",
        status=status,
        raw_payload={"device_id": str(device.pk), "success_count": success_count, "failure_count": failure_count},
        error_message=error_message,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
    )
    return ModbusPollOutcome(str(device.pk), str(ingest_id), status, success_count, failure_count, error_message)
