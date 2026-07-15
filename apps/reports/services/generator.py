from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, time
from typing import Iterable

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.utils.text import slugify

from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.common.audit import write_audit
from apps.devices.models import Device
from apps.notifications.models import Notification, NotificationStatus
from apps.telemetry.models import MetricDefinition, TelemetryPoint

from ..constants import SUPPORTED_REPORT_TYPES
from ..models import ReportJob, ReportJobStatus

logger = logging.getLogger(__name__)

def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write report audit log.", exc_info=True)
        return None


def _resolve_report_type(job: ReportJob) -> str:
    report_type = job.report_type
    if not report_type:
        raise ValueError("Report type is missing from the report template or parameters.")
    report_type = str(report_type).strip()
    if report_type not in SUPPORTED_REPORT_TYPES:
        raise ValueError(f"Unsupported report type: {report_type}")
    return report_type


def _get_parameter_value(parameters: dict, *keys: str):
    for key in keys:
        value = parameters.get(key)
        if value not in (None, ""):
            return value
    return None


def _make_aware(value: datetime):
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _parse_range_value(raw_value, *, is_end: bool) -> datetime:
    if isinstance(raw_value, datetime):
        return _make_aware(raw_value)
    if isinstance(raw_value, date):
        raw_datetime = datetime.combine(raw_value, time.max if is_end else time.min)
        return _make_aware(raw_datetime)
    if isinstance(raw_value, str):
        parsed_dt = parse_datetime(raw_value)
        if parsed_dt is not None:
            return _make_aware(parsed_dt)
        parsed_date = parse_date(raw_value)
        if parsed_date is not None:
            raw_datetime = datetime.combine(parsed_date, time.max if is_end else time.min)
            return _make_aware(raw_datetime)
    raise ValueError(f"Invalid date value: {raw_value!r}")


def _extract_date_range(job: ReportJob):
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    start_value = _get_parameter_value(parameters, "date_from", "start_date")
    end_value = _get_parameter_value(parameters, "date_to", "end_date")
    if start_value is None and end_value is None:
        return None, None

    start_dt = _parse_range_value(start_value, is_end=False) if start_value is not None else None
    end_dt = _parse_range_value(end_value, is_end=True) if end_value is not None else None

    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("Invalid date range: date_from/start_date must be earlier than date_to/end_date.")
    return start_dt, end_dt


def _apply_date_range(queryset, job: ReportJob, field_name: str):
    start_dt, end_dt = _extract_date_range(job)
    if start_dt:
        queryset = queryset.filter(**{f"{field_name}__gte": start_dt})
    if end_dt:
        queryset = queryset.filter(**{f"{field_name}__lte": end_dt})
    return queryset


def _report_filename(job: ReportJob, report_type: str) -> str:
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    org_label = getattr(job.organization, "code", None) or getattr(job.organization, "name", None) or str(job.organization_id)
    org_slug = slugify(str(org_label)) or str(job.organization_id)
    return f"{org_slug}/{report_type}_{job.pk}_{timestamp}.csv"


def _render_csv(headers: Iterable[str], rows: Iterable[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(headers))
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in headers})
    return buffer.getvalue().encode("utf-8")


def _alert_summary_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    qs = AlertEvent.objects.filter(organization_id=job.organization_id)
    if job.data_center_id:
        qs = qs.filter(data_center_id=job.data_center_id)
    qs = _apply_date_range(qs, job, "triggered_at")

    active_qs = qs.filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
    summary_rows = [
        {"section": "summary", "label": "open_total", "value": active_qs.count()},
        {"section": "summary", "label": "critical_open", "value": active_qs.filter(severity=AlertSeverity.CRITICAL).count()},
        {"section": "summary", "label": "warning_open", "value": active_qs.filter(severity=AlertSeverity.WARNING).count()},
        {"section": "summary", "label": "acknowledged_total", "value": qs.filter(status=AlertStatus.ACKNOWLEDGED).count()},
        {"section": "summary", "label": "resolved_total", "value": qs.filter(status=AlertStatus.RESOLVED).count()},
        {"section": "summary", "label": "unacknowledged_critical", "value": qs.filter(status=AlertStatus.OPEN, severity=AlertSeverity.CRITICAL).count()},
    ]

    severity_rows = [
        {"section": "severity", "label": row["severity"], "value": row["total"]}
        for row in active_qs.values("severity").annotate(total=Count("id")).order_by("severity")
    ]
    status_rows = [
        {"section": "status", "label": row["status"], "value": row["total"]}
        for row in qs.values("status").annotate(total=Count("id")).order_by("status")
    ]

    return ["section", "label", "value"], summary_rows + severity_rows + status_rows


def _notification_delivery_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    qs = Notification.objects.filter(organization_id=job.organization_id)
    if job.data_center_id:
        alert_event_ids = list(
            AlertEvent.objects.filter(
                organization_id=job.organization_id,
                data_center_id=job.data_center_id,
            ).values_list("id", flat=True)
        )
        if alert_event_ids:
            qs = qs.filter(metadata__alert_event_id__in=[str(alert_id) for alert_id in alert_event_ids])
        else:
            qs = qs.none()
    qs = _apply_date_range(qs, job, "created_at")

    summary_rows = [
        {"section": "summary", "label": "total", "value": qs.count()},
        {"section": "summary", "label": "pending", "value": qs.filter(status=NotificationStatus.PENDING).count()},
        {"section": "summary", "label": "delivering", "value": qs.filter(status=NotificationStatus.DELIVERING).count()},
        {"section": "summary", "label": "sent", "value": qs.filter(status=NotificationStatus.SENT).count()},
        {"section": "summary", "label": "failed", "value": qs.filter(status=NotificationStatus.FAILED).count()},
        {"section": "summary", "label": "read_count", "value": qs.filter(read_at__isnull=False).count()},
    ]
    rows = summary_rows + [
        {"section": "status", "label": row["status"], "value": row["total"]}
        for row in qs.values("status").annotate(total=Count("id")).order_by("status")
    ]
    rows.extend(
        {"section": "channel", "label": row["channel"], "value": row["total"]}
        for row in qs.values("channel").annotate(total=Count("id")).order_by("channel")
    )
    return ["section", "label", "value"], rows


def _device_inventory_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    qs = Device.objects.select_related("organization", "data_center", "room", "rack", "device_type", "device_model__vendor").filter(
        organization_id=job.organization_id
    )
    if job.data_center_id:
        qs = qs.filter(data_center_id=job.data_center_id)

    headers = [
        "device_id",
        "device_name",
        "device_code",
        "organization_name",
        "data_center_name",
        "room_name",
        "rack_name",
        "device_type_name",
        "vendor_name",
        "status",
        "is_active",
        "last_seen_at",
    ]
    rows = [
        {
            "device_id": str(device.id),
            "device_name": device.name,
            "device_code": device.code,
            "organization_name": getattr(device.organization, "name", None),
            "data_center_name": getattr(device.data_center, "name", None),
            "room_name": getattr(device.room, "name", None),
            "rack_name": getattr(device.rack, "name", None),
            "device_type_name": getattr(device.device_type, "name", None),
            "vendor_name": getattr(getattr(device.device_model, "vendor", None), "name", None),
            "status": device.status,
            "is_active": device.is_active,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        }
        for device in qs.order_by("name", "code")
    ]
    return headers, rows


def _telemetry_value(point: TelemetryPoint):
    if point.value_float is not None:
        return point.value_float
    if point.value_integer is not None:
        return point.value_integer
    if point.value_boolean is not None:
        return point.value_boolean
    if point.value_text is not None:
        return point.value_text
    return point.raw_value_text


def _room_environment_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    metric_codes = parameters.get("metrics")
    if not isinstance(metric_codes, (list, tuple)):
        metric_codes = []
    metric_codes = [str(code).strip() for code in metric_codes if str(code).strip()]
    if not metric_codes and job.template_id and isinstance(getattr(job.template, "config", None), dict):
        template_metrics = job.template.config.get("metrics")
        if isinstance(template_metrics, (list, tuple)):
            metric_codes = [str(code).strip() for code in template_metrics if str(code).strip()]
    if not metric_codes:
        metric_codes = ["room_temperature", "room_humidity"]

    metrics = MetricDefinition.objects.filter(code__in=metric_codes, is_active=True)
    if not metrics.exists():
        headers = [
            "room_name",
            "room_code",
            "device_name",
            "device_code",
            "metric_code",
            "metric_name",
            "measured_at",
            "value",
            "unit",
            "quality",
            "source",
        ]
        return headers, []

    qs = (
        TelemetryPoint.objects.select_related("device__room", "metric")
        .filter(organization_id=job.organization_id, metric_id__in=metrics.values_list("id", flat=True))
    )
    if job.data_center_id:
        qs = qs.filter(data_center_id=job.data_center_id)
    qs = _apply_date_range(qs, job, "time")

    headers = [
        "room_name",
        "room_code",
        "device_name",
        "device_code",
        "metric_code",
        "metric_name",
        "measured_at",
        "value",
        "unit",
        "quality",
        "source",
    ]
    rows = []
    for point in qs.order_by("device__room__name", "device__name", "time", "metric__code"):
        room = getattr(point.device, "room", None)
        metric = point.metric
        rows.append(
            {
                "room_name": getattr(room, "name", None) or "Unassigned",
                "room_code": getattr(room, "code", None) or "",
                "device_name": getattr(point.device, "name", None),
                "device_code": getattr(point.device, "code", None),
                "metric_code": getattr(metric, "code", None),
                "metric_name": getattr(metric, "name", None),
                "measured_at": point.time.isoformat() if point.time else None,
                "value": _telemetry_value(point),
                "unit": getattr(metric, "unit", None) or "",
                "quality": point.quality,
                "source": point.source or "",
            }
        )
    return headers, rows


def _build_report(job: ReportJob) -> tuple[str, bytes]:
    report_type = _resolve_report_type(job)
    if report_type == "alert_summary":
        headers, rows = _alert_summary_rows(job)
    elif report_type == "notification_delivery":
        headers, rows = _notification_delivery_rows(job)
    elif report_type == "device_inventory":
        headers, rows = _device_inventory_rows(job)
    elif report_type == "room_environment":
        headers, rows = _room_environment_rows(job)
    else:
        raise ValueError(f"Unsupported report type: {report_type}")

    return report_type, _render_csv(headers, rows)


def generate_report_job(report_job_id):
    now = timezone.now()
    with transaction.atomic():
        job = (
            ReportJob.objects.select_for_update()
            .filter(pk=report_job_id)
            .first()
        )
        if not job:
            raise ValueError(f"Report job {report_job_id} does not exist.")

        if job.status == ReportJobStatus.CANCELLED:
            logger.info("Skipping cancelled report job=%s", job.pk)
            return job
        if job.status not in {ReportJobStatus.PENDING, ReportJobStatus.FAILED}:
            logger.info("Skipping report job not eligible for generation job=%s status=%s", job.pk, job.status)
            return job

        job.status = ReportJobStatus.PROCESSING
        job.started_at = job.started_at or now
        job.error_message = ""
        job.save(update_fields=["status", "started_at", "error_message", "updated_at"])

        _safe_write_audit(
            "REPORT_GENERATION_STARTED",
            "ReportJob",
            job.pk,
        organization=job.organization,
        actor=job.requested_by,
        message=f"Report generation started for {job.report_type or 'unknown'}",
    )

    try:
        report_type, content = _build_report(job)
        filename = _report_filename(job, report_type)
        with transaction.atomic():
            job = ReportJob.objects.select_for_update().get(pk=job.pk)
            if job.status == ReportJobStatus.CANCELLED:
                logger.info("Report job cancelled during generation job=%s", job.pk)
                return job
            if job.file:
                job.file.delete(save=False)
            job.file.save(filename, ContentFile(content), save=False)
            job.status = ReportJobStatus.COMPLETED
            job.completed_at = timezone.now()
            job.error_message = ""
            job.save(update_fields=["file", "status", "completed_at", "error_message", "updated_at"])

        _safe_write_audit(
            "REPORT_GENERATED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=job.requested_by,
            message=f"Report generated successfully for {report_type}",
        )
        return job
    except Exception as exc:
        logger.exception("Report generation failed report_job=%s", report_job_id)
        with transaction.atomic():
            job = ReportJob.objects.select_for_update().filter(pk=report_job_id).first()
            if job:
                job.status = ReportJobStatus.FAILED
                job.completed_at = timezone.now()
                job.error_message = str(exc)
                job.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
                _safe_write_audit(
                    "REPORT_GENERATION_FAILED",
                    "ReportJob",
                    job.pk,
                    organization=job.organization,
                    actor=job.requested_by,
                    message=str(exc),
                )
        return job
