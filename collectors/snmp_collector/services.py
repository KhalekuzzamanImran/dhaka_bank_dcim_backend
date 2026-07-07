import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.utils import timezone

from apps.alerts.services import evaluate_latest
from apps.devices.models import (
    Device,
    DeviceCredential,
    DevicePollingConfig,
    DeviceProtocolConfig,
    DeviceStatus,
    ProtocolType,
    SNMPOIDMapping,
)
from apps.telemetry.models import LatestTelemetry, TelemetryIngestLog, TelemetryPoint, TelemetryQuality
from .client import SNMPClient, SNMPResult
from .exceptions import SNMPConfigurationError, SNMPCredentialError, SNMPResponseError, SNMPTimeoutError, SNMPWorkerError
from collectors.common.snmp_normalization import apply_scale_offset, parse_snmp_raw_value, store_value_by_metric_type

logger = logging.getLogger(__name__)


@dataclass
class PollOutcome:
    device_id: str
    ingest_id: str
    status: str
    success_count: int
    failure_count: int
    error_message: Optional[str] = None


def get_enabled_snmp_devices_due(limit: int = 100) -> List[str]:
    now = timezone.now()
    queryset = (
        DevicePollingConfig.objects.select_related("device", "polling_profile")
        .filter(
            is_enabled=True,
            polling_profile__is_active=True,
            polling_profile__protocol=ProtocolType.SNMP,
            device__is_active=True,
        )
        .filter(next_poll_at__lte=now)
        .order_by("next_poll_at")
    )
    return [str(row.device_id) for row in queryset[:limit]]


def get_device_snmp_runtime(device: Device) -> Tuple[DeviceProtocolConfig, DeviceCredential, List[SNMPOIDMapping]]:
    protocol_config = (
        DeviceProtocolConfig.objects.filter(device=device, protocol=ProtocolType.SNMP, is_enabled=True)
        .order_by("-is_primary", "created_at")
        .first()
    )
    if not protocol_config:
        raise SNMPConfigurationError("SNMP protocol config is missing or disabled")

    credential = (
        DeviceCredential.objects.filter(device=device, protocol=ProtocolType.SNMP, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    if not credential:
        raise SNMPCredentialError("Active SNMP credential is missing")

    mappings = list(
        SNMPOIDMapping.objects.select_related("metric")
        .filter(device_type=device.device_type, is_active=True)
        .filter(device_model__isnull=True) | SNMPOIDMapping.objects.select_related("metric")
        .filter(device_model=device.device_model, is_active=True)
    )
    # Avoid duplicates when both generic and model-specific mappings exist. Model-specific wins.
    deduped = {}
    for mapping in mappings:
        key = mapping.metric_id
        current = deduped.get(key)
        if current is None or mapping.device_model_id:
            deduped[key] = mapping
    mappings = list(deduped.values())
    if not mappings:
        raise SNMPConfigurationError("No active SNMP OID mappings found for this device type/model")
    return protocol_config, credential, mappings


def _apply_scale(raw: Any, mapping: SNMPOIDMapping) -> Any:
    parsed_value = parse_snmp_raw_value(raw, mapping.data_type)
    return apply_scale_offset(parsed_value, mapping.scale_factor, mapping.offset_value)


def _value_payload(metric_data_type: str, value: Any) -> Dict[str, Any]:
    return store_value_by_metric_type(None, metric_data_type, value)


def _quality_for_metric(metric_data_type: str, payload: Dict[str, Any]) -> str:
    metric_type = str(metric_data_type or "").strip().upper()
    if metric_type == "FLOAT" and payload.get("value_float") is None:
        return TelemetryQuality.BAD
    if metric_type == "INTEGER" and payload.get("value_integer") is None:
        return TelemetryQuality.BAD
    if metric_type == "BOOLEAN" and payload.get("value_boolean") is None:
        return TelemetryQuality.BAD
    if metric_type in {"TEXT", "STRING", "STR"} and payload.get("value_text") in (None, ""):
        return TelemetryQuality.BAD
    return TelemetryQuality.GOOD


def _mark_success(device: Device, polling_config: Optional[DevicePollingConfig], at):
    updates = {"status": DeviceStatus.ONLINE, "last_seen_at": at}
    Device.objects.filter(pk=device.pk).update(**updates)
    if polling_config:
        interval = polling_config.polling_profile.interval_seconds
        DevicePollingConfig.objects.filter(pk=polling_config.pk).update(
            last_polled_at=at,
            next_poll_at=at + timezone.timedelta(seconds=interval),
            consecutive_failures=0,
            last_error_message="",
        )


def _mark_failure(device: Device, polling_config: Optional[DevicePollingConfig], error_message: str, at):
    failure_count = 1
    stale_after = 180
    interval = 60
    if polling_config:
        failure_count = polling_config.consecutive_failures + 1
        stale_after = polling_config.polling_profile.stale_after_seconds
        interval = polling_config.polling_profile.interval_seconds
    status = DeviceStatus.DEGRADED
    if device.last_seen_at is None or (at - device.last_seen_at).total_seconds() >= stale_after or failure_count >= 3:
        status = DeviceStatus.OFFLINE
    Device.objects.filter(pk=device.pk).update(status=status)
    if polling_config:
        DevicePollingConfig.objects.filter(pk=polling_config.pk).update(
            last_polled_at=at,
            next_poll_at=at + timezone.timedelta(seconds=min(interval, 300)),
            consecutive_failures=failure_count,
            last_error_message=error_message[:2000],
        )


def poll_snmp_device(device_id: str, evaluate_alerts: bool = True) -> PollOutcome:
    started_at = timezone.now()
    ingest_id = uuid.uuid4()
    success_count = 0
    failure_count = 0
    error_message = None
    device = Device.objects.select_related("organization", "data_center", "device_type", "device_model").get(pk=device_id)
    polling_config = getattr(device, "polling_config", None)

    try:
        protocol_config, credential, mappings = get_device_snmp_runtime(device)
        client = SNMPClient(protocol_config, credential)
        with transaction.atomic():
            for mapping in mappings:
                try:
                    result: SNMPResult = client.get(mapping.oid)
                    parsed_raw_value = parse_snmp_raw_value(result.raw_value, mapping.data_type)
                    final_value = apply_scale_offset(parsed_raw_value, mapping.scale_factor, mapping.offset_value)
                    payload = _value_payload(mapping.metric.data_type, final_value)
                    quality = _quality_for_metric(mapping.metric.data_type, payload)
                    point = TelemetryPoint.objects.create(
                        time=started_at,
                        organization=device.organization,
                        data_center=device.data_center,
                        device=device,
                        metric=mapping.metric,
                        quality=quality,
                        source="snmp_worker",
                        ingest_id=ingest_id,
                        raw_value_text=result.raw_value,
                        **payload,
                    )
                    latest, _ = LatestTelemetry.objects.update_or_create(
                        device=device,
                        metric=mapping.metric,
                        defaults={
                            "organization": device.organization,
                            "data_center": device.data_center,
                            "quality": quality,
                            "last_seen_at": started_at,
                            "source": "snmp_worker",
                            "raw_value_text": result.raw_value,
                            **payload,
                        },
                    )
                    if evaluate_alerts:
                        evaluate_latest(latest)
                    success_count += 1
                except Exception as exc:
                    failure_count += 1
                    logger.warning("SNMP OID poll failed device=%s oid=%s error=%s", device.pk, mapping.oid, exc)
            if success_count == 0:
                raise SNMPResponseError("All configured SNMP OIDs failed")
            _mark_success(device, polling_config, started_at)
        status = "SUCCESS" if failure_count == 0 else "PARTIAL_SUCCESS"
    except SoftTimeLimitExceeded:
        error_message = "SNMP poll exceeded worker soft time limit"
        _mark_failure(device, polling_config, error_message, timezone.now())
        status = "FAILED"
    except Exception as exc:
        error_message = str(exc)
        _mark_failure(device, polling_config, error_message, timezone.now())
        status = "FAILED"
        logger.exception("SNMP poll failed device=%s", device.pk)

    finished_at = timezone.now()
    TelemetryIngestLog.objects.create(
        ingest_id=ingest_id,
        device=device,
        protocol="SNMP",
        status=status,
        raw_payload={
            "device_id": str(device.pk),
            "success_count": success_count,
            "failure_count": failure_count,
        },
        error_message=error_message,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
    )
    return PollOutcome(str(device.pk), str(ingest_id), status, success_count, failure_count, error_message)
