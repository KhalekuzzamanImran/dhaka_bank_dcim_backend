from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.utils import timezone

from ..models import AlertSeverity, AlertStatus
from ..serializers import AlertEventListSerializer

ACTIVE_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)


def _active_queryset(queryset):
    return queryset.filter(status__in=ACTIVE_STATUSES)


def _coerce_timezone(business_timezone=None):
    if business_timezone is None:
        return timezone.get_current_timezone()
    if hasattr(business_timezone, "utcoffset"):
        return business_timezone
    try:
        return ZoneInfo(str(business_timezone))
    except Exception:
        return timezone.get_current_timezone()


def _today_in_timezone(business_timezone=None):
    tz = _coerce_timezone(business_timezone)
    return timezone.localtime(timezone.now(), tz).date()


def _day_bounds_in_timezone(business_timezone=None):
    tz = _coerce_timezone(business_timezone)
    today = _today_in_timezone(tz)
    start = timezone.make_aware(datetime.combine(today, dt_time.min), tz)
    end = start + timedelta(days=1)
    return start, end


def _severity_dict(queryset):
    return {
        row["severity"]: row["total"]
        for row in queryset.values("severity").annotate(total=Count("id")).order_by("severity")
    }


def _status_dict(queryset):
    return {
        row["status"]: row["total"]
        for row in queryset.values("status").annotate(total=Count("id")).order_by("status")
    }


def build_alert_summary(queryset, business_timezone=None):
    active_qs = _active_queryset(queryset)
    start_of_day, end_of_day = _day_bounds_in_timezone(business_timezone)
    return {
        "open_total": active_qs.count(),
        "critical_open": active_qs.filter(severity=AlertSeverity.CRITICAL).count(),
        "warning_open": active_qs.filter(severity=AlertSeverity.WARNING).count(),
        "acknowledged_total": queryset.filter(status=AlertStatus.ACKNOWLEDGED).count(),
        "resolved_today": queryset.filter(status=AlertStatus.RESOLVED, resolved_at__gte=start_of_day, resolved_at__lt=end_of_day).count(),
        "unacknowledged_critical": queryset.filter(status=AlertStatus.OPEN, severity=AlertSeverity.CRITICAL).count(),
        "by_severity": _severity_dict(active_qs),
        "by_status": _status_dict(queryset),
    }


def build_active_by_severity(queryset):
    active_qs = _active_queryset(queryset)
    return list(
        active_qs.values("severity")
        .annotate(total=Count("id"))
        .order_by("severity")
    )


def build_top_devices(queryset, limit=10):
    active_qs = _active_queryset(queryset).exclude(device_id__isnull=True)
    rows = (
        active_qs.values("device_id", "device__name", "device__code")
        .annotate(
            alert_count=Count("id"),
            critical_count=Count("id", filter=Q(severity=AlertSeverity.CRITICAL)),
        )
        .order_by("-alert_count", "-critical_count", "device__name")[:limit]
    )
    return [
        {
            "device_id": row["device_id"],
            "device_name": row["device__name"],
            "device_code": row["device__code"],
            "alert_count": row["alert_count"],
            "critical_count": row["critical_count"],
            "device__name": row["device__name"],
            "total": row["alert_count"],
        }
        for row in rows
    ]


def build_recent_alerts(queryset, limit=10, context=None):
    serializer = AlertEventListSerializer(queryset.order_by("-triggered_at")[:limit], many=True, context=context or {})
    return serializer.data


def build_dashboard_payload(queryset, business_timezone=None):
    data = build_alert_summary(queryset, business_timezone=business_timezone)
    active_qs = _active_queryset(queryset)
    data["by_data_center"] = {
        row["data_center__name"]: row["total"]
        for row in active_qs.values("data_center__name").annotate(total=Count("id")).order_by("data_center__name")
    }
    data["by_device_type"] = {
        row["device__device_type__name"]: row["total"]
        for row in active_qs.values("device__device_type__name").annotate(total=Count("id")).order_by("device__device_type__name")
    }
    return data
