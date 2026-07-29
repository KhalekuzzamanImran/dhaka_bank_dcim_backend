from __future__ import annotations

from django.core.cache import cache
from django.db.models import Count

from apps.alerts.models import AlertEvent
from apps.alerts.services.summary import build_alert_summary, build_recent_alerts
from apps.telemetry.models import LatestTelemetry


OVERVIEW_SUMMARY_CACHE_KEY = "dcim:overview-summary:version"
OVERVIEW_SUMMARY_CACHE_TTL_SECONDS = 30


def _isoformat(value):
    return value.isoformat() if value else None


def bump_overview_summary_cache_version():
    try:
        cache.add(OVERVIEW_SUMMARY_CACHE_KEY, 1, None)
        cache.incr(OVERVIEW_SUMMARY_CACHE_KEY)
    except Exception:
        current = cache.get(OVERVIEW_SUMMARY_CACHE_KEY, 1)
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = 1
        cache.set(OVERVIEW_SUMMARY_CACHE_KEY, current + 1, None)


def _get_cache_version():
    version = cache.get(OVERVIEW_SUMMARY_CACHE_KEY, 1)
    try:
        return int(version)
    except (TypeError, ValueError):
        return 1


def _build_overview_summary(device_queryset, *, active_alert_limit: int = 50):
    devices = list(
        device_queryset.values(
            "id",
            "name",
            "code",
            "status",
            "is_active",
            "last_seen_at",
            "device_type_id",
            "device_type__name",
            "device_type__code",
            "organization__name",
            "data_center__name",
            "room__name",
            "rack__name",
        )
    )

    device_ids = [row["id"] for row in devices]
    if not device_ids:
        return {
            "devices": [],
            "device_types": [],
            "latest_telemetry": [],
            "active_alerts": [],
            "alert_summary": build_alert_summary(AlertEvent.objects.none()),
        }

    device_type_rows = (
        device_queryset.values("device_type_id", "device_type__name", "device_type__code")
        .annotate(device_count=Count("id"))
        .order_by("device_type__name", "device_type__code")
    )
    device_types = [
        {
            "id": row["device_type_id"],
            "name": row["device_type__name"],
            "code": row["device_type__code"],
            "device_count": row["device_count"],
        }
        for row in device_type_rows
        if row["device_type_id"] is not None
    ]

    latest_rows = (
        LatestTelemetry.objects.filter(device_id__in=device_ids)
        .select_related("device", "metric")
        .values(
            "device_id",
            "device__name",
            "metric__code",
            "metric__name",
            "value_float",
            "value_integer",
            "value_boolean",
            "value_text",
            "quality",
            "last_seen_at",
        )
        .order_by("device_id", "metric__code")
    )
    latest_telemetry = [
        {
            "device_id": str(row["device_id"]),
            "device_name": row["device__name"],
            "metric_code": row["metric__code"],
            "metric_name": row["metric__name"],
            "value_float": row["value_float"],
            "value_integer": row["value_integer"],
            "value_boolean": row["value_boolean"],
            "value_text": row["value_text"],
            "quality": row["quality"],
            "last_seen_at": _isoformat(row["last_seen_at"]),
        }
        for row in latest_rows
    ]

    alert_queryset = AlertEvent.objects.select_related(
        "organization",
        "data_center",
        "device",
        "device__device_type",
        "metric",
        "alert_rule",
        "acknowledged_by",
        "resolved_by",
    ).filter(device_id__in=device_ids)
    active_alerts = build_recent_alerts(
        alert_queryset.filter(status__in=("OPEN", "ACKNOWLEDGED")),
        limit=active_alert_limit,
    )

    return {
        "devices": [
            {
                **row,
                "id": str(row["id"]),
                "device_type": str(row["device_type_id"]) if row["device_type_id"] is not None else None,
                "device_type_name": row["device_type__name"],
                "device_type_code": row["device_type__code"],
                "organization_name": row["organization__name"],
                "data_center_name": row["data_center__name"],
                "room_name": row["room__name"],
                "rack_name": row["rack__name"],
                "last_seen_at": _isoformat(row["last_seen_at"]),
            }
            for row in devices
        ],
        "device_types": device_types,
        "latest_telemetry": latest_telemetry,
        "active_alerts": active_alerts,
        "alert_summary": build_alert_summary(alert_queryset),
    }


def build_data_center_overview_summary(device_queryset, *, active_alert_limit: int = 50, cache_scope: str = "global"):
    cache_version = _get_cache_version()
    cache_key = f"dcim:overview-summary:{cache_version}:{cache_scope}:{active_alert_limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _build_overview_summary(device_queryset, active_alert_limit=active_alert_limit)
    cache.set(cache_key, payload, OVERVIEW_SUMMARY_CACHE_TTL_SECONDS)
    return payload
