import logging
from typing import Dict, Optional

from django.db import transaction
from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.devices.models import Device
from apps.telemetry.models import DeviceEvent, DeviceEventSeverity, MetricDataType, LatestTelemetry
from apps.traps.models import SNMPTrapEvent
from collectors.common.telemetry_writer import write_device_telemetry_bulk
from collectors.snmp_collector.client import SNMPClient
from collectors.snmp_collector.services import _apply_scale, get_device_snmp_runtime

logger = logging.getLogger(__name__)

PAC_CONFIRMATION_TRAP_CODES = {"PAC_ALARM_FIRED", "PAC_ALARM_RESTORED"}
PAC_ALARM_METRIC_CODES = {
    "GENERAL_ALARM",
    "ROOM_HIGH_TEMPERATURE_ALARM",
    "ROOM_LOW_TEMPERATURE_ALARM",
    "ROOM_HIGH_HUMIDITY_ALARM",
    "ROOM_LOW_HUMIDITY_ALARM",
    "FILTER_ALARM",
    "WATER_LEAK_ALARM",
    "AIRFLOW_ALARM",
    "PHASE_SEQUENCE_ALARM",
    "SMOKE_FIRE_ALARM",
    "LAN_ALARM",
    "EMERGENCY_POWER_ALARM",
}


def _normalize_code(code: Optional[str]) -> str:
    return (code or "").strip().upper().removeprefix("PAC_")


def is_pac_device(device: Device) -> bool:
    device_type = getattr(device, "device_type", None)
    if not device_type:
        return False
    code = (device_type.code or "").strip().upper()
    name = (device_type.name or "").strip().upper()
    return code == "PAC" or "PAC" in name


def is_pac_confirmation_trap(event_code: Optional[str]) -> bool:
    return (event_code or "").strip().upper() in PAC_CONFIRMATION_TRAP_CODES


def is_pac_alarm_metric(metric_code: Optional[str]) -> bool:
    return _normalize_code(metric_code) in PAC_ALARM_METRIC_CODES


def get_pac_alarm_mappings(device: Device):
    _, _, mappings = get_device_snmp_runtime(device)
    return [mapping for mapping in mappings if is_pac_alarm_metric(mapping.metric.code)]


def _coerce_alarm_state(value) -> Optional[int]:
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None:
        return None
    if isinstance(value, int):
        if value == 1:
            return 1
        if value == 0:
            return 0
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "active", "alarm"}:
            return 1
        if normalized in {"0", "false", "no", "off", "normal", "restored"}:
            return 0
        try:
            numeric = int(normalized)
            if numeric == 1:
                return 1
            if numeric == 0:
                return 0
            return None
        except Exception:
            return None
    try:
        numeric = int(value)
        if numeric == 1:
            return 1
        if numeric == 0:
            return 0
        return None
    except Exception:
        return None


def _telemetry_payload(metric, state: int) -> Dict[str, object]:
    if metric.data_type == MetricDataType.FLOAT:
        return {"value_float": float(state)}
    if metric.data_type == MetricDataType.INTEGER:
        return {"value_integer": int(state)}
    if metric.data_type == MetricDataType.BOOLEAN:
        return {"value_boolean": bool(state)}
    return {"value_text": str(state)}


def _alert_message(state: int) -> str:
    if state == 1:
        return "PAC alarm confirmed by SNMP poll"
    return "PAC alarm restored and confirmed by SNMP poll"


def _open_or_update_exact_alert(*, device: Device, metric, state: int, confirmation_at, payload: Dict[str, object]):
    open_alert = (
        AlertEvent.objects.select_for_update()
        .filter(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            metric=metric,
            alert_rule__isnull=True,
            status=AlertStatus.OPEN,
        )
        .order_by("-triggered_at", "-created_at")
        .first()
    )
    if open_alert:
        open_alert.severity = AlertSeverity.CRITICAL
        open_alert.message = _alert_message(state)
        open_alert.value_float = payload.get("value_float")
        open_alert.value_integer = payload.get("value_integer")
        open_alert.value_boolean = payload.get("value_boolean")
        open_alert.value_text = payload.get("value_text")
        open_alert.save(update_fields=["severity", "message", "value_float", "value_integer", "value_boolean", "value_text", "updated_at"])
        return open_alert, False

    alert = AlertEvent.objects.create(
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        metric=metric,
        alert_rule=None,
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.OPEN,
        message=_alert_message(state),
        triggered_at=confirmation_at,
        value_float=payload.get("value_float"),
        value_integer=payload.get("value_integer"),
        value_boolean=payload.get("value_boolean"),
        value_text=payload.get("value_text"),
    )
    return alert, True


def _resolve_exact_alert(*, device: Device, metric, confirmation_at, payload: Dict[str, object]):
    open_alert = (
        AlertEvent.objects.select_for_update()
        .filter(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            metric=metric,
            alert_rule__isnull=True,
            status=AlertStatus.OPEN,
        )
        .order_by("-triggered_at", "-created_at")
        .first()
    )
    if not open_alert:
        return None, False

    open_alert.status = AlertStatus.RESOLVED
    open_alert.resolved_at = confirmation_at
    open_alert.message = _alert_message(0)
    open_alert.value_float = payload.get("value_float")
    open_alert.value_integer = payload.get("value_integer")
    open_alert.value_boolean = payload.get("value_boolean")
    open_alert.value_text = payload.get("value_text")
    open_alert.save(
        update_fields=[
            "status",
            "resolved_at",
            "message",
            "value_float",
            "value_integer",
            "value_boolean",
            "value_text",
            "updated_at",
        ]
    )
    return open_alert, True


def _create_device_event(*, device: Device, metric, trap_event: SNMPTrapEvent, state: int, confirmation_at, raw_payload: Dict[str, object]):
    event_code = "PAC_ALARM_CONFIRMED_OPEN" if state == 1 else "PAC_ALARM_CONFIRMED_RESOLVED"
    severity = DeviceEventSeverity.CRITICAL if state == 1 else DeviceEventSeverity.INFO
    DeviceEvent.objects.create(
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        event_code=event_code,
        event_name=metric.name,
        severity=severity,
        message=_alert_message(state),
        occurred_at=confirmation_at,
        raw_payload={
            "trap_event_id": str(trap_event.id),
            "trap_event_code": trap_event.event_code,
            "trap_oid": trap_event.trap_oid,
            "metric_code": metric.code,
            "confirmed_state": state,
            **raw_payload,
        },
    )


def _log_failure_event(*, device: Device, trap_event: SNMPTrapEvent, message: str):
    DeviceEvent.objects.create(
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        event_code="PAC_CONFIRMATION_FAILED",
        event_name="PAC Trap Confirmation Failed",
        severity=DeviceEventSeverity.WARNING,
        message=message,
        occurred_at=timezone.now(),
        raw_payload={
            "trap_event_id": str(trap_event.id),
            "trap_event_code": trap_event.event_code,
            "trap_oid": trap_event.trap_oid,
        },
    )


def process_pac_trap_alarm_confirmation(*, device_id: str, trap_event_id: str, event_code: Optional[str]):
    """Poll PAC alarm/status OIDs after a trap to confirm the real alarm state.

    SNMP traps are fast and useful for signaling that something changed, but they can
    be lost or duplicated over UDP. This step re-polls the device to confirm the
    exact alarm value before opening or resolving the durable alert record.
    """

    trap_event = (
        SNMPTrapEvent.objects.select_related(
            "organization",
            "data_center",
            "device",
            "device__device_type",
            "device__device_model",
        )
        .filter(pk=trap_event_id, device_id=device_id)
        .first()
    )
    if not trap_event or not trap_event.device:
        logger.warning("PAC confirmation skipped because trap event or device was missing trap_event=%s device=%s", trap_event_id, device_id)
        return {"status": "skipped", "reason": "missing_trap_event_or_device"}

    device = trap_event.device
    if not is_pac_device(device) or not is_pac_confirmation_trap(event_code):
        return {"status": "skipped", "reason": "non_pac_trap"}

    try:
        protocol_config, credential, _ = get_device_snmp_runtime(device)
    except Exception as exc:
        message = f"PAC confirmation could not load SNMP runtime: {exc}"
        logger.warning("%s device=%s trap_event=%s", message, device.pk, trap_event.pk)
        _log_failure_event(device=device, trap_event=trap_event, message=message)
        return {"status": "failed", "reason": "runtime_error", "error": str(exc)}

    mappings = get_pac_alarm_mappings(device)
    if not mappings:
        message = "PAC confirmation found no active alarm/status OID mappings"
        logger.warning("%s device=%s trap_event=%s", message, device.pk, trap_event.pk)
        _log_failure_event(device=device, trap_event=trap_event, message=message)
        return {"status": "failed", "reason": "no_alarm_mappings"}

    client = SNMPClient(protocol_config, credential)
    confirmation_at = timezone.now()
    processed = 0
    failures = 0

    for mapping in mappings:
        try:
            result = client.get(mapping.oid)
            scaled_value = _apply_scale(result.value, mapping)
            state = _coerce_alarm_state(scaled_value)
            if state is None:
                raise ValueError(f"Unrecognized PAC alarm value for {mapping.metric.code}: {scaled_value!r}")

            write_device_telemetry_bulk(
                organization=device.organization,
                data_center=device.data_center,
                device=device,
                readings=[{"metric": mapping.metric, "value": state, "raw_value_text": result.raw_value}],
                source="TRAP_CONFIRMED_POLL",
                ingest_id=trap_event.id,
                timestamp=confirmation_at,
            )
            latest = LatestTelemetry.objects.select_related("organization", "data_center", "device", "metric").get(
                device=device,
                metric=mapping.metric,
            )
            from apps.alerts.services import evaluate_latest

            evaluate_latest(latest)

            _create_device_event(
                device=device,
                metric=mapping.metric,
                trap_event=trap_event,
                state=state,
                confirmation_at=confirmation_at,
                raw_payload={
                    "source_ip": trap_event.source_ip,
                    "oid": mapping.oid,
                    "raw_snmp_value": result.raw_value,
                    "scaled_value": scaled_value,
                },
            )
            processed += 1
        except Exception as exc:
            failures += 1
            logger.exception("PAC confirmation OID poll failed device=%s oid=%s trap_event=%s error=%s", device.pk, mapping.oid, trap_event.pk, exc)
            _log_failure_event(
                device=device,
                trap_event=trap_event,
                message=f"PAC confirmation poll failed for {mapping.metric.code}: {exc}",
            )

    return {
        "status": "success" if processed and failures == 0 else "partial_success" if processed else "failed",
        "processed_count": processed,
        "failure_count": failures,
        "trap_event_id": str(trap_event.id),
    }
