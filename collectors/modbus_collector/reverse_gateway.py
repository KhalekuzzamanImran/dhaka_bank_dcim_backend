"""Collector for VPN-reachable gateways that initiate a reverse TCP bridge.

The gateway opens the TCP connection; this service remains the Modbus client and
issues ordinary Modbus TCP requests over that socket. Gateways are identified by
their stable, allow-listed VPN source IP (not by an unauthenticated payload).
"""

from __future__ import annotations

import logging
import select
import signal
import socket
import struct
import threading
import time
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.alerts.services import evaluate_latest
from apps.devices.models import (
    Device,
    DevicePollingConfig,
    DeviceProtocolConfig,
    DeviceStatus,
    ModbusGateway,
    ModbusGatewayConnectionStatus,
    ModbusGatewayWriteRequest,
    ModbusGatewayWriteStatus,
    ModbusRegisterMapping,
    ProtocolType,
)
from apps.live_updates.services import publish_device_status_update
from apps.telemetry.models import LatestTelemetry, TelemetryIngestLog, TelemetryQuality
from collectors.common.locks import acquire_device_poll_lock, release_device_poll_lock
from collectors.common.telemetry_writer import write_device_telemetry_bulk
from collectors.common.value_converter import normalize_value
from .services import _mark_failure, _mark_success

logger = logging.getLogger(__name__)

BATTERY_SOH_FALLBACK_PERCENT = Decimal("84.4")


def apply_battery_soh_fallback(metric_code, value):
    """Use the configured operational fallback only when the Acrel SOH register is zero."""
    if metric_code == "battery_soh" and value == 0:
        return BATTERY_SOH_FALLBACK_PERCENT, TelemetryQuality.ESTIMATED
    return value, TelemetryQuality.GOOD


class ModbusGatewayProtocolError(Exception):
    """A malformed or mismatched Modbus TCP frame was received."""


class ModbusGatewayRemoteError(Exception):
    """A remote Modbus exception response was received."""


def build_read_request(transaction_id: int, mapping: ModbusRegisterMapping) -> bytes:
    if mapping.function_code not in {1, 2, 3, 4}:
        raise ModbusGatewayProtocolError(f"Unsupported function code {mapping.function_code}.")
    if not 0 <= int(mapping.slave_id) <= 255:
        raise ModbusGatewayProtocolError("Modbus slave ID must be between 0 and 255.")
    quantity = int(mapping.register_count)
    if not 1 <= quantity <= 125:
        raise ModbusGatewayProtocolError("Modbus read quantity must be between 1 and 125.")
    pdu = struct.pack(">BHH", int(mapping.function_code), int(mapping.register_address), quantity)
    return struct.pack(">HHHB", transaction_id, 0, 1 + len(pdu), int(mapping.slave_id)) + pdu


def _receive_exact(conn: socket.socket, size: int) -> bytes:
    received = bytearray()
    while len(received) < size:
        chunk = conn.recv(size - len(received))
        if not chunk:
            raise ConnectionError("Gateway closed the TCP connection.")
        received.extend(chunk)
    return bytes(received)


def _decode_registers(registers: list[int], mapping: ModbusRegisterMapping):
    data_type = str(mapping.data_type or "uint16").strip().lower()
    if data_type in {"uint16", "integer", "int", "gauge", "counter"}:
        return int(registers[0])
    if data_type == "int16":
        value = int(registers[0])
        return value - 0x10000 if value & 0x8000 else value
    if data_type in {"uint32", "int32", "float32"}:
        if len(registers) < 2:
            raise ModbusGatewayProtocolError(f"{data_type} requires two registers.")
        chunks = [int(register).to_bytes(2, "big") for register in registers[:2]]
        if str(mapping.byte_order or "BIG").upper() == "LITTLE":
            chunks = [chunk[::-1] for chunk in chunks]
        if str(mapping.word_order or "BIG").upper() == "LITTLE":
            chunks.reverse()
        raw = b"".join(chunks)
        if data_type == "float32":
            return struct.unpack(">f", raw)[0]
        number = int.from_bytes(raw, "big", signed=data_type == "int32")
        return number
    if data_type in {"float", "float64"}:
        if len(registers) >= 4:
            chunks = [int(register).to_bytes(2, "big") for register in registers[:4]]
            if str(mapping.byte_order or "BIG").upper() == "LITTLE":
                chunks = [chunk[::-1] for chunk in chunks]
            if str(mapping.word_order or "BIG").upper() == "LITTLE":
                chunks.reverse()
            return struct.unpack(">d", b"".join(chunks))[0]
        return float(registers[0])
    return int(registers[0])


def read_mapping(conn: socket.socket, mapping: ModbusRegisterMapping, transaction_id: int):
    conn.sendall(build_read_request(transaction_id, mapping))
    header = _receive_exact(conn, 7)
    response_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
    if protocol_id != 0 or length < 3 or length > 254:
        raise ModbusGatewayProtocolError("Invalid Modbus TCP response header.")
    if response_id != transaction_id or unit_id != mapping.slave_id:
        raise ModbusGatewayProtocolError("Modbus transaction or slave ID did not match the request.")
    pdu = _receive_exact(conn, length - 1)
    function = int(mapping.function_code)
    if len(pdu) < 2:
        raise ModbusGatewayProtocolError("Modbus response PDU is incomplete.")
    if pdu[0] & 0x80:
        raise ModbusGatewayRemoteError(f"Modbus exception response: function={pdu[0]}, code={pdu[1]}.")
    if pdu[0] != function:
        raise ModbusGatewayProtocolError("Modbus function code did not match the request.")

    count = int(mapping.register_count)
    byte_count = pdu[1]
    payload = pdu[2:]
    if len(payload) != byte_count:
        raise ModbusGatewayProtocolError("Modbus byte count does not match the response payload.")
    if function in {1, 2}:
        expected = (count + 7) // 8
        if byte_count != expected:
            raise ModbusGatewayProtocolError("Modbus bit response has an unexpected byte count.")
        return bool(payload[0] & 1)
    if byte_count != count * 2:
        raise ModbusGatewayProtocolError("Modbus register response has an unexpected byte count.")
    registers = list(struct.unpack(f">{count}H", payload))
    return _decode_registers(registers, mapping)


def _write_single_register(conn: socket.socket, *, slave_id: int, address: int, value: int, transaction_id: int):
    """Write one holding register (FC06), then verify by reading it back (FC03)."""
    pdu = struct.pack(">BHH", 6, address, value)
    request = struct.pack(">HHHB", transaction_id, 0, 1 + len(pdu), slave_id) + pdu
    conn.sendall(request)
    header = _receive_exact(conn, 7)
    response_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
    response_pdu = _receive_exact(conn, length - 1)
    if protocol_id != 0 or response_id != transaction_id or unit_id != slave_id:
        raise ModbusGatewayProtocolError("Modbus write response did not match the request.")
    if len(response_pdu) != 5 or response_pdu[0] != 6:
        if response_pdu and response_pdu[0] & 0x80:
            raise ModbusGatewayRemoteError(f"Modbus write exception code {response_pdu[1]}.")
        raise ModbusGatewayProtocolError("Invalid Modbus write response.")
    if response_pdu != pdu:
        raise ModbusGatewayProtocolError("Modbus device did not echo the written register and value.")

    verify_id = (transaction_id + 1) & 0xFFFF or 1
    read_pdu = struct.pack(">BHH", 3, address, 1)
    conn.sendall(struct.pack(">HHHB", verify_id, 0, 1 + len(read_pdu), slave_id) + read_pdu)
    header = _receive_exact(conn, 7)
    response_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
    verify_response = _receive_exact(conn, length - 1)
    if protocol_id != 0 or response_id != verify_id or unit_id != slave_id:
        raise ModbusGatewayProtocolError("Modbus read-back response did not match the request.")
    if len(verify_response) != 4 or verify_response[0] != 3 or verify_response[1] != 2:
        if verify_response and verify_response[0] & 0x80:
            raise ModbusGatewayRemoteError(f"Modbus read-back exception code {verify_response[1]}.")
        raise ModbusGatewayProtocolError("Invalid Modbus read-back response.")
    return struct.unpack(">H", verify_response[2:])[0]


def _process_pending_gateway_write(gateway, conn, transaction_counter):
    request = ModbusGatewayWriteRequest.objects.filter(
        gateway=gateway, status=ModbusGatewayWriteStatus.PENDING,
    ).order_by("created_at").first()
    if request is None:
        return False
    # Claim before touching the shared gateway socket; the listener has one worker per gateway.
    changed = ModbusGatewayWriteRequest.objects.filter(
        pk=request.pk, status=ModbusGatewayWriteStatus.PENDING,
    ).update(status=ModbusGatewayWriteStatus.PROCESSING, error_message="")
    if not changed:
        return False
    try:
        config = DeviceProtocolConfig.objects.get(
            device=request.device, modbus_gateway=gateway,
            protocol=ProtocolType.MODBUS_RTU_GATEWAY, is_enabled=True,
        )
        if not request.device.is_active:
            raise ValueError("Target device is inactive.")
        conn.settimeout(max(1, int(config.timeout_seconds or settings.MODBUS_GATEWAY_RESPONSE_TIMEOUT_SECONDS)))
        value = _write_single_register(
            conn, slave_id=request.slave_id, address=request.register_address,
            value=request.register_value, transaction_id=transaction_counter[0],
        )
        if value != request.register_value:
            raise ValueError(f"Read-back mismatch: wrote {request.register_value}, read {value}.")
        ModbusGatewayWriteRequest.objects.filter(pk=request.pk).update(
            status=ModbusGatewayWriteStatus.SUCCEEDED, response_value=value,
            completed_at=timezone.now(),
        )
    except Exception as exc:
        ModbusGatewayWriteRequest.objects.filter(pk=request.pk).update(
            status=ModbusGatewayWriteStatus.FAILED, error_message=str(exc)[:2000],
            completed_at=timezone.now(),
        )
        if isinstance(exc, (ConnectionError, OSError, socket.timeout)):
            raise
        logger.exception("Modbus gateway register write failed gateway=%s request=%s", gateway.code, request.pk)
    finally:
        transaction_counter[0] = (transaction_counter[0] + 2) & 0xFFFF or 1
    return True


def _active_mappings(device):
    mappings = ModbusRegisterMapping.objects.select_related("metric", "vendor", "device_model").filter(
        device_type=device.device_type,
        is_active=True,
    ).filter(
        models.Q(device_model=device.device_model) | models.Q(device_model__isnull=True)
    ).filter(
        models.Q(vendor=device.device_model.vendor if device.device_model_id else None)
        | models.Q(vendor__isnull=True)
    )
    selected = {}
    for mapping in mappings.order_by("metric_id", "-device_model_id", "-vendor_id"):
        specificity = (mapping.device_model_id is not None, mapping.vendor_id is not None)
        previous = selected.get(mapping.metric_id)
        if previous is None or specificity > previous[0]:
            selected[mapping.metric_id] = (specificity, mapping)
    return [item[1] for item in selected.values()]


def _gateway_device_configs(gateway):
    return list(
        DeviceProtocolConfig.objects.select_related(
            "device",
            "device__organization",
            "device__data_center",
            "device__device_type",
            "device__device_model",
            "device__device_model__vendor",
            "device__polling_config__polling_profile",
        ).filter(
            modbus_gateway=gateway,
            protocol=ProtocolType.MODBUS_RTU_GATEWAY,
            is_enabled=True,
            device__is_active=True,
            device__polling_config__is_enabled=True,
            device__polling_config__polling_profile__is_active=True,
            device__polling_config__polling_profile__protocol=ProtocolType.MODBUS_RTU_GATEWAY,
        )
    )


def _mark_gateway_device(gateway, protocol_config, conn, transaction_counter):
    device = protocol_config.device
    polling_config = device.polling_config
    started_at = timezone.now()
    ingest_id = uuid.uuid4()
    success_count = 0
    failure_count = 0
    error_message = ""
    readings = []
    transport_error = None
    try:
        mappings = _active_mappings(device)
        if not mappings:
            raise ValueError("No active Modbus register mappings match this device.")
        for mapping in mappings:
            transaction_id = transaction_counter[0]
            try:
                raw_value = read_mapping(conn, mapping, transaction_id)
                transaction_counter[0] = (transaction_id + 1) & 0xFFFF or 1
                value = normalize_value(raw_value, mapping.data_type, mapping.scale_factor, mapping.offset_value)
                if value is None:
                    raise ValueError("Register value could not be converted using its configured data type.")
                value, quality = apply_battery_soh_fallback(mapping.metric.code, value)
                readings.append({
                    "metric": mapping.metric,
                    "value": value,
                    "quality": quality,
                    "raw_value_text": str(raw_value),
                })
                success_count += 1
            except (socket.timeout, ConnectionError, OSError, ModbusGatewayProtocolError) as exc:
                transaction_counter[0] = (transaction_id + 1) & 0xFFFF or 1
                transport_error = exc
                failure_count += 1
                error_message = f"Gateway transport failed at register {mapping.register_address}: {exc}"
                break
            except ModbusGatewayRemoteError as exc:
                transaction_counter[0] = (transaction_id + 1) & 0xFFFF or 1
                failure_count += 1
                error_message = f"Register {mapping.register_address}: {exc}"
                logger.warning("Reverse Modbus device exception gateway=%s device=%s register=%s error=%s", gateway.code, device.pk, mapping.register_address, exc)
            except Exception as exc:
                transaction_counter[0] = (transaction_id + 1) & 0xFFFF or 1
                failure_count += 1
                error_message = f"Register {mapping.register_address}: {exc}"
                logger.warning("Reverse Modbus mapping failed gateway=%s device=%s register=%s error=%s", gateway.code, device.pk, mapping.register_address, exc)

        if readings:
            write_device_telemetry_bulk(
                organization=device.organization,
                data_center=device.data_center,
                device=device,
                readings=readings,
                source="modbus_rtu_gateway",
                ingest_id=ingest_id,
                timestamp=started_at,
            )
            for latest in LatestTelemetry.objects.filter(device=device, metric__in=[row["metric"] for row in readings]):
                evaluate_latest(latest)

        if success_count:
            _mark_success(device, polling_config, started_at, source="modbus_rtu_gateway")
            status = "SUCCESS" if not failure_count else "PARTIAL_SUCCESS"
        else:
            error_message = error_message or "No Modbus registers were read successfully."
            _mark_failure(device, polling_config, error_message, timezone.now(), source="modbus_rtu_gateway")
            status = "FAILED"
    except Exception as exc:
        error_message = str(exc)
        failure_count += 1
        _mark_failure(device, polling_config, error_message, timezone.now(), source="modbus_rtu_gateway")
        status = "FAILED"
        logger.exception("Reverse Modbus poll failed gateway=%s device=%s", gateway.code, device.pk)
    finally:
        finished_at = timezone.now()
        TelemetryIngestLog.objects.create(
            ingest_id=ingest_id,
            device=device,
            protocol=ProtocolType.MODBUS_RTU_GATEWAY,
            status=status if "status" in locals() else "FAILED",
            raw_payload={
                "gateway_id": str(gateway.pk),
                "gateway_code": gateway.code,
                "device_id": str(device.pk),
                "success_count": success_count,
                "failure_count": failure_count,
            },
            error_message=error_message or None,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        )
    return transport_error


class ModbusReverseGatewayServer:
    def __init__(self, host=None, port=None):
        self.host = host or settings.MODBUS_GATEWAY_LISTEN_HOST
        self.port = int(port or settings.MODBUS_GATEWAY_LISTEN_PORT)
        self._stopping = threading.Event()
        self._active_gateways: set[str] = set()
        self._active_lock = threading.Lock()
        self._listener = None

    def stop(self):
        self._stopping.set()
        if self._listener:
            try:
                self._listener.close()
            except OSError:
                pass

    def _handle_connection(self, conn, peer, gateway):
        gateway_id = str(gateway.pk)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        conn.settimeout(max(1, int(settings.MODBUS_GATEWAY_RESPONSE_TIMEOUT_SECONDS)))
        connected_at = timezone.now()
        ModbusGateway.objects.filter(pk=gateway.pk).update(
            connection_status=ModbusGatewayConnectionStatus.CONNECTED,
            last_connected_at=connected_at,
            last_error="",
        )
        logger.info("Modbus reverse gateway connected code=%s peer=%s:%s", gateway.code, peer[0], peer[1])
        transaction_counter = [1]
        no_devices_reported = False
        try:
            while not self._stopping.is_set():
                configs = _gateway_device_configs(gateway)
                now = timezone.now()
                polled = _process_pending_gateway_write(gateway, conn, transaction_counter)
                if not configs and not no_devices_reported:
                    logger.warning("Connected Modbus gateway has no enabled battery device configs code=%s", gateway.code)
                    no_devices_reported = True
                elif configs:
                    no_devices_reported = False
                for protocol_config in configs:
                    polling_config = protocol_config.device.polling_config
                    if polling_config.next_poll_at and polling_config.next_poll_at > now:
                        continue
                    if not acquire_device_poll_lock(protocol_config.device_id, timeout=max(30, int(polling_config.polling_profile.interval_seconds) * 2)):
                        continue
                    polled = True
                    try:
                        conn.settimeout(max(1, int(protocol_config.timeout_seconds or settings.MODBUS_GATEWAY_RESPONSE_TIMEOUT_SECONDS)))
                        transport_error = _mark_gateway_device(gateway, protocol_config, conn, transaction_counter)
                    finally:
                        release_device_poll_lock(protocol_config.device_id)
                    if transport_error:
                        raise transport_error
                if not configs:
                    logger.warning("Connected Modbus gateway has no enabled battery device configs code=%s", gateway.code)
                if not polled:
                    readable, _, _ = select.select([conn], [], [], 0.5)
                    if readable:
                        peek = conn.recv(1, socket.MSG_PEEK)
                        if not peek:
                            raise ConnectionError("Gateway closed the TCP connection.")
                        raise ModbusGatewayProtocolError("Unexpected unsolicited bytes received from gateway.")
        except (ConnectionError, OSError, socket.timeout, ModbusGatewayProtocolError) as exc:
            if not self._stopping.is_set():
                logger.warning("Modbus reverse gateway disconnected code=%s error=%s", gateway.code, exc)
                ModbusGateway.objects.filter(pk=gateway.pk).update(last_error=str(exc)[:2000])
        except Exception as exc:
            if not self._stopping.is_set():
                logger.exception("Modbus reverse gateway session failed code=%s", gateway.code)
                ModbusGateway.objects.filter(pk=gateway.pk).update(last_error=str(exc)[:2000])
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            disconnected_at = timezone.now()
            ModbusGateway.objects.filter(pk=gateway.pk).update(
                connection_status=ModbusGatewayConnectionStatus.DISCONNECTED,
                last_disconnected_at=disconnected_at,
            )
            with self._active_lock:
                self._active_gateways.discard(gateway_id)

    def serve_forever(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(64)
        listener.settimeout(1.0)
        self._listener = listener
        logger.info("Modbus reverse gateway listener started host=%s port=%s", self.host, self.port)
        try:
            while not self._stopping.is_set():
                try:
                    conn, peer = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise
                gateway = ModbusGateway.objects.filter(source_ip=peer[0], is_active=True).first()
                if not gateway:
                    logger.warning("Rejected unregistered Modbus gateway peer=%s:%s", peer[0], peer[1])
                    conn.close()
                    continue
                gateway_id = str(gateway.pk)
                with self._active_lock:
                    if gateway_id in self._active_gateways:
                        logger.warning("Rejected duplicate connection for gateway code=%s peer=%s", gateway.code, peer[0])
                        conn.close()
                        continue
                    self._active_gateways.add(gateway_id)
                threading.Thread(
                    target=self._handle_connection,
                    args=(conn, peer, gateway),
                    name=f"modbus-gateway-{gateway.code}",
                    daemon=True,
                ).start()
        finally:
            listener.close()
            self._listener = None
            logger.info("Modbus reverse gateway listener stopped")


def main():
    server = ModbusReverseGatewayServer()
    previous_handlers = {}

    def request_stop(signum, frame):
        server.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, request_stop)
    try:
        server.serve_forever()
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

