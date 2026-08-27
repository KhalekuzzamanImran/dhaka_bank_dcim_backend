from __future__ import annotations

from typing import Iterable

from apps.alerts.models import AlertEvent, AlertEventLog, AlertStatus
from apps.telemetry.models import DeviceEvent
from apps.traps.models import SNMPTrapEvent

ACTIVE_ALERT_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)
DEFAULT_ACTIVE_LIMIT = 10
DEFAULT_RECENT_LIMIT = 25


def _isoformat(value):
    return value.isoformat() if value else None


def _display_name(user) -> str | None:
    if not user:
        return None
    full_name = getattr(user, "get_full_name", None)
    if callable(full_name):
        name = full_name().strip()
        if name:
            return name
    return getattr(user, "username", None) or str(user)


def _active_alarm_payload(alert: AlertEvent) -> dict:
    return {
        "id": str(alert.pk),
        "source_type": "alert_event",
        "severity": alert.severity,
        "status": alert.status,
        "title": alert.message,
        "message": alert.message,
        "triggered_at": _isoformat(alert.triggered_at),
        "last_seen_at": _isoformat(alert.last_seen_at),
        "acknowledged_at": _isoformat(alert.acknowledged_at),
        "resolved_at": _isoformat(alert.resolved_at),
        "occurrence_count": alert.occurrence_count,
        "alert_event_id": str(alert.pk),
        "metadata": alert.metadata or {},
    }


def _alert_log_payload(log: AlertEventLog) -> dict:
    return {
        "id": str(log.pk),
        "source_type": "alert_event_log",
        "source_label": "Alert Event Log",
        "timestamp": _isoformat(log.created_at),
        "severity": log.alert_event.severity,
        "status": log.new_status or log.old_status or log.alert_event.status,
        "title": log.get_action_display(),
        "message": log.message or log.alert_event.message,
        "action": log.action,
        "alert_event_id": str(log.alert_event_id),
        "alert_message": log.alert_event.message,
        "old_status": log.old_status,
        "new_status": log.new_status,
        "actor": log.actor_id,
        "actor_name": _display_name(log.actor),
        "metadata": log.metadata or {},
        "value_snapshot": log.value_snapshot,
    }


def _device_event_payload(event: DeviceEvent) -> dict:
    return {
        "id": str(event.pk),
        "source_type": "device_event",
        "source_label": "Device Event",
        "timestamp": _isoformat(event.occurred_at),
        "severity": event.severity,
        "status": None,
        "title": event.event_name,
        "message": event.message,
        "event_code": event.event_code,
        "device_event_id": str(event.pk),
        "metadata": event.raw_payload or {},
    }


def _trap_event_payload(event: SNMPTrapEvent) -> dict:
    return {
        "id": str(event.pk),
        "source_type": "snmp_trap_event",
        "source_label": "SNMP Trap",
        "timestamp": _isoformat(event.received_at),
        "severity": event.severity,
        "status": None,
        "title": event.event_name or event.event_code or "SNMP Trap",
        "message": event.message,
        "trap_oid": event.trap_oid,
        "event_code": event.event_code,
        "resolution_source": event.resolution_source,
        "is_mapped": event.is_mapped,
        "requires_mapping_review": event.requires_mapping_review,
        "trap_event_id": str(event.pk),
        "metadata": event.raw_varbinds or {},
    }


def _sort_recent_events(items: Iterable[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: item.get("_sort_timestamp"),
        reverse=True,
    )


def build_device_activity_feed(device, *, active_limit: int = DEFAULT_ACTIVE_LIMIT, recent_limit: int = DEFAULT_RECENT_LIMIT) -> dict:
    """Build a combined operational activity feed for a single device."""

    active_alerts_qs = (
        AlertEvent.objects.select_related(
            "organization",
            "data_center",
            "device",
            "metric",
            "alert_rule",
            "acknowledged_by",
            "resolved_by",
        )
        .filter(device=device, status__in=ACTIVE_ALERT_STATUSES)
        .order_by("-triggered_at", "-last_seen_at", "-created_at")
    )
    active_alerts = [_active_alarm_payload(alert) for alert in active_alerts_qs[: max(0, int(active_limit or 0))]]

    recent_alert_logs_qs = (
        AlertEventLog.objects.select_related("alert_event", "alert_event__device", "actor")
        .filter(alert_event__device=device)
        .order_by("-created_at")
    )
    recent_device_events_qs = DeviceEvent.objects.select_related("organization", "data_center", "device").filter(device=device).order_by("-occurred_at")
    recent_trap_events_qs = SNMPTrapEvent.objects.select_related("organization", "data_center", "device").filter(device=device).order_by("-received_at")

    alert_event_logs = []
    for log in recent_alert_logs_qs[: max(0, int(recent_limit or 0))]:
        item = _alert_log_payload(log)
        item["_sort_timestamp"] = log.created_at
        alert_event_logs.append(item)

    device_events = []
    for event in recent_device_events_qs[: max(0, int(recent_limit or 0))]:
        item = _device_event_payload(event)
        item["_sort_timestamp"] = event.occurred_at
        device_events.append(item)

    snmp_trap_events = []
    for event in recent_trap_events_qs[: max(0, int(recent_limit or 0))]:
        item = _trap_event_payload(event)
        item["_sort_timestamp"] = event.received_at
        snmp_trap_events.append(item)

    recent_items: list[dict] = [*alert_event_logs, *device_events, *snmp_trap_events]
    recent_events = _sort_recent_events(recent_items)[: max(0, int(recent_limit or 0))]

    for collection in (alert_event_logs, device_events, snmp_trap_events, recent_events):
        for item in collection:
            item.pop("_sort_timestamp", None)

    return {
        "device_id": str(device.pk),
        "active_alarms_count": len(active_alerts),
        "recent_events_count": len(recent_events),
        "alert_event_logs_count": len(alert_event_logs),
        "device_events_count": len(device_events),
        "snmp_trap_events_count": len(snmp_trap_events),
        "active_alarms": active_alerts,
        "alert_event_logs": alert_event_logs,
        "device_events": device_events,
        "snmp_trap_events": snmp_trap_events,
        "recent_events": recent_events,
    }
