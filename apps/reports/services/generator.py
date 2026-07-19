from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, time, timedelta
from typing import Iterable

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.utils.text import slugify

from apps.alerts.models import AlertEvent, AlertEventLogAction, AlertSeverity, AlertStatus
from apps.audit.models import AuditAction, AuditLog
from apps.common.audit import write_audit
from apps.accounts.models import User
from apps.datacenters.models import DataCenter, Rack, Room
from apps.devices.models import Device, DeviceModel, DeviceStatus, DeviceType
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.telemetry.models import MetricDefinition, TelemetryPoint

from ..constants import SUPPORTED_REPORT_TYPES, normalize_key, normalize_report_format
from ..models import ReportJob, ReportJobStatus

logger = logging.getLogger(__name__)

def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write report audit log.", exc_info=True)
        return None


def _format_validation_message(exc: ValidationError) -> str:
    messages: list[str] = []
    if getattr(exc, "messages", None):
        messages = [str(message) for message in exc.messages if str(message)]
    elif getattr(exc, "message_dict", None):
        for values in exc.message_dict.values():
            messages.extend(str(value) for value in values if str(value))
    return "; ".join(messages) if messages else str(exc)


def _fail_report_job(report_job: ReportJob, message: str):
    with transaction.atomic():
        job = ReportJob.objects.select_for_update().filter(pk=report_job.pk).first()
        if not job:
            return report_job
        job.status = ReportJobStatus.FAILED
        job.completed_at = timezone.now()
        job.error_message = message
        job.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
        _safe_write_audit(
            "REPORT_GENERATION_FAILED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=job.requested_by,
            message=message,
        )
        return job


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


def _get_report_config(job: ReportJob) -> dict:
    config = getattr(job.template, "config", None)
    return config if isinstance(config, dict) else {}


def _normalize_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace(";", ",").split(",")
    normalized = [str(item).strip() for item in raw_values if str(item).strip()]
    deduplicated = []
    seen = set()
    for item in normalized:
        if item in seen:
            continue
        seen.add(item)
        deduplicated.append(item)
    return deduplicated


def _normalize_choice_values(value, choices: list[str], *, field_name: str) -> list[str]:
    values = _normalize_list(value)
    if not values:
        return []
    lookup = {normalize_key(choice): choice for choice in choices}
    normalized: list[str] = []
    for raw_value in values:
        choice = lookup.get(normalize_key(raw_value))
        if not choice:
            raise ValueError(f"Invalid {field_name} value: {raw_value}")
        if choice not in normalized:
            normalized.append(choice)
    return normalized


def _normalize_bool(value, *, field_name: str) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"Invalid {field_name} value: {value}")


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


def parse_report_date_range(parameters: dict, *, required: bool = False):
    start_value = _get_parameter_value(parameters, "date_from", "start_date")
    end_value = _get_parameter_value(parameters, "date_to", "end_date")
    if start_value is None and end_value is None:
        if required:
            raise ValueError("date_from and date_to are required.")
        return None, None
    if required and (start_value is None or end_value is None):
        raise ValueError("date_from and date_to are required.")

    start_dt = _parse_range_value(start_value, is_end=False) if start_value is not None else None
    end_dt = _parse_range_value(end_value, is_end=True) if end_value is not None else None
    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("Invalid date range: date_from/start_date must be earlier than date_to/end_date.")
    return start_dt, end_dt


def validate_max_date_range(date_from: datetime | None, date_to: datetime | None, template_config: dict, default_days: int = 90):
    if not date_from or not date_to:
        return
    max_days = template_config.get("max_date_range_days", default_days)
    try:
        max_days = int(max_days)
    except (TypeError, ValueError):
        max_days = default_days
    if max_days <= 0:
        return
    if date_to - date_from > timedelta(days=max_days):
        raise ValueError(f"Date range cannot exceed {max_days} days.")


def _apply_date_range(queryset, job: ReportJob, field_name: str):
    start_dt, end_dt = parse_report_date_range(job.parameters if isinstance(job.parameters, dict) else {})
    if start_dt:
        queryset = queryset.filter(**{f"{field_name}__gte": start_dt})
    if end_dt:
        queryset = queryset.filter(**{f"{field_name}__lte": end_dt})
    return queryset


def _resolve_data_center_scope(report_job: ReportJob, parameters: dict, *, required: bool = False) -> DataCenter | None:
    job_data_center = report_job.data_center
    parameter_data_center_id = parameters.get("data_center_id")

    if parameter_data_center_id in (None, ""):
        if required and not job_data_center:
            raise ValueError("data_center_id is required.")
        return job_data_center

    try:
        parameter_data_center = DataCenter.objects.select_related("organization").get(pk=parameter_data_center_id)
    except (DataCenter.DoesNotExist, ValueError, TypeError):
        raise ValueError("Invalid data_center_id.")

    if parameter_data_center.organization_id != report_job.organization_id:
        raise ValueError("data_center_id must belong to the selected organization.")
    if job_data_center and job_data_center.pk != parameter_data_center.pk:
        raise ValueError("ReportJob.data_center must match parameters.data_center_id.")
    return parameter_data_center


def _resolve_device(report_job: ReportJob, device_id) -> Device:
    try:
        device = (
            Device.objects.select_related("organization", "data_center", "device_model__vendor", "device_type", "room", "rack")
            .get(pk=device_id)
        )
    except (Device.DoesNotExist, ValueError, TypeError):
        raise ValueError("Invalid device_id.")
    if device.organization_id != report_job.organization_id:
        raise ValueError("device_id must belong to the selected organization.")
    return device


def _resolve_room(report_job: ReportJob, room_id) -> Room:
    try:
        room = Room.objects.select_related("data_center").get(pk=room_id)
    except (Room.DoesNotExist, ValueError, TypeError):
        raise ValueError("Invalid room_id.")
    if room.data_center.organization_id != report_job.organization_id:
        raise ValueError("room_id must belong to the selected organization.")
    return room


def _resolve_rack(report_job: ReportJob, rack_id) -> Rack:
    try:
        rack = Rack.objects.select_related("data_center", "room").get(pk=rack_id)
    except (Rack.DoesNotExist, ValueError, TypeError):
        raise ValueError("Invalid rack_id.")
    if rack.data_center.organization_id != report_job.organization_id:
        raise ValueError("rack_id must belong to the selected organization.")
    return rack


def _resolve_device_model(report_job: ReportJob, device_model_id) -> DeviceModel:
    try:
        device_model = DeviceModel.objects.select_related("vendor", "device_type").get(pk=device_model_id)
    except (DeviceModel.DoesNotExist, ValueError, TypeError):
        raise ValueError("Invalid device_model_id.")
    if not Device.objects.filter(organization_id=report_job.organization_id, device_model_id=device_model.pk).exists():
        raise ValueError("device_model_id must belong to the selected organization.")
    return device_model


def _resolve_device_type(report_job: ReportJob, device_type_id) -> DeviceType:
    try:
        device_type = DeviceType.objects.get(pk=device_type_id)
    except (DeviceType.DoesNotExist, ValueError, TypeError):
        raise ValueError("Invalid device_type_id.")
    if not Device.objects.filter(organization_id=report_job.organization_id, device_type_id=device_type.pk).exists():
        raise ValueError("device_type_id must belong to the selected organization.")
    return device_type


def _resolve_metric_codes(parameters: dict, *, required: bool = False) -> list[str]:
    codes = _normalize_list(parameters.get("metric_codes"))
    if required and not codes:
        raise ValueError("metric_codes is required.")
    return codes


# Older scheduled reports used generic environment names before metric codes
# were device-specific. Keep those jobs runnable without accepting unknown
# metric names or changing the canonical codes stored in the database.
TELEMETRY_METRIC_CODE_ALIASES = {
    "room_temperature": "pac_room_temperature",
    "room_humidity": "pac_room_humidity",
    "roomTemp": "pac_room_temperature",
    "roomRH": "pac_room_humidity",
}


def _resolve_telemetry_metrics(metric_codes: list[str]) -> tuple[list[str], list[MetricDefinition]]:
    exact_metrics = {
        metric.code: metric
        for metric in MetricDefinition.objects.filter(code__in=metric_codes)
    }
    canonical_codes = []
    for code in metric_codes:
        if code in exact_metrics:
            canonical_codes.append(code)
            continue
        target = TELEMETRY_METRIC_CODE_ALIASES.get(code)
        canonical_codes.append(target or code)

    metrics_by_code = {
        metric.code: metric
        for metric in MetricDefinition.objects.filter(code__in=set(canonical_codes))
    }
    missing_codes = [
        requested
        for requested, canonical in zip(metric_codes, canonical_codes)
        if canonical not in metrics_by_code
    ]
    if missing_codes:
        raise ValueError(f"Invalid metric_codes value: {', '.join(missing_codes)}.")

    # Preserve the user's selection order while removing duplicate aliases.
    ordered_metrics = []
    seen = set()
    for canonical in canonical_codes:
        if canonical in seen:
            continue
        seen.add(canonical)
        ordered_metrics.append(metrics_by_code[canonical])
    return canonical_codes, ordered_metrics


def _resolve_output_format(job: ReportJob) -> str:
    config = _get_report_config(job)
    raw_output_format = config.get("output_format", "csv")
    normalized = normalize_report_format(raw_output_format)
    if not normalized:
        if normalize_key(raw_output_format) == "xlsx":
            raise ValueError("XLSX output is not supported yet.")
        raise ValueError(f"{raw_output_format} output is not supported yet.")
    if normalized != "CSV":
        if normalized == "XLSX":
            raise ValueError("XLSX output is not supported yet.")
        raise ValueError(f"{normalized} output is not supported yet.")

    allowed_output_formats = _normalize_list(config.get("allowed_output_formats"))
    if allowed_output_formats and "csv" not in {normalize_key(value) for value in allowed_output_formats}:
        raise ValueError("CSV output is not allowed for this template.")
    return "CSV"


def _filter_queryset_by_report_scope(queryset, report_job: ReportJob, parameters: dict, *, field_name: str = "data_center"):
    data_center = _resolve_data_center_scope(report_job, parameters)
    if report_job.data_center_id:
        queryset = queryset.filter(**{field_name: report_job.data_center_id})
    if data_center and data_center.pk != report_job.data_center_id:
        queryset = queryset.filter(**{field_name: data_center.pk})
    return queryset, data_center


def _organization_label(instance):
    return getattr(instance, "name", None) or getattr(instance, "code", None) or ""


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
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    template_config = _get_report_config(job)
    date_from, date_to = parse_report_date_range(parameters)
    validate_max_date_range(date_from, date_to, template_config)

    qs = AlertEvent.objects.select_related(
        "organization",
        "data_center",
        "device",
        "device__device_model",
        "device__device_type",
        "device__room",
        "device__rack",
        "metric",
        "acknowledged_by",
        "resolved_by",
    ).filter(organization_id=job.organization_id)

    data_center = _resolve_data_center_scope(job, parameters)
    if data_center:
        qs = qs.filter(data_center_id=data_center.pk)

    if date_from:
        qs = qs.filter(triggered_at__gte=date_from)
    if date_to:
        qs = qs.filter(triggered_at__lte=date_to)

    metric_codes = _resolve_metric_codes(parameters)
    if metric_codes:
        qs = qs.filter(metric__code__in=metric_codes)

    severities = _normalize_choice_values(parameters.get("severity"), list(AlertSeverity.values), field_name="severity")
    if severities:
        qs = qs.filter(severity__in=severities)

    statuses = _normalize_choice_values(parameters.get("status"), list(AlertStatus.values), field_name="status")
    if statuses:
        qs = qs.filter(status__in=statuses)

    device_id = parameters.get("device_id")
    if device_id not in (None, ""):
        device = _resolve_device(job, device_id)
        if data_center and device.data_center_id != data_center.pk:
            raise ValueError("device_id must belong to the selected data center.")
        qs = qs.filter(device_id=device.pk)

    device_model_id = parameters.get("device_model_id")
    if device_model_id not in (None, ""):
        device_model = _resolve_device_model(job, device_model_id)
        qs = qs.filter(device__device_model_id=device_model.pk)

    device_type_id = parameters.get("device_type_id")
    if device_type_id not in (None, ""):
        device_type = _resolve_device_type(job, device_type_id)
        qs = qs.filter(device__device_type_id=device_type.pk)

    room_id = parameters.get("room_id")
    if room_id not in (None, ""):
        room = _resolve_room(job, room_id)
        if data_center and room.data_center_id != data_center.pk:
            raise ValueError("room_id must belong to the selected data center.")
        qs = qs.filter(device__room_id=room.pk)

    rack_id = parameters.get("rack_id")
    if rack_id not in (None, ""):
        rack = _resolve_rack(job, rack_id)
        if data_center and rack.data_center_id != data_center.pk:
            raise ValueError("rack_id must belong to the selected data center.")
        qs = qs.filter(device__rack_id=rack.pk)

    active_qs = qs.filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
    summary_rows = [
        {"section": "summary", "label": "total_alerts", "value": qs.count()},
        {"section": "summary", "label": "open_total", "value": active_qs.count()},
        {"section": "summary", "label": "critical_open", "value": active_qs.filter(severity=AlertSeverity.CRITICAL).count()},
        {"section": "summary", "label": "warning_open", "value": active_qs.filter(severity=AlertSeverity.WARNING).count()},
        {"section": "summary", "label": "acknowledged_total", "value": qs.filter(status=AlertStatus.ACKNOWLEDGED).count()},
        {"section": "summary", "label": "resolved_total", "value": qs.filter(status=AlertStatus.RESOLVED).count()},
        {"section": "summary", "label": "unacknowledged_critical", "value": qs.filter(status=AlertStatus.OPEN, severity=AlertSeverity.CRITICAL).count()},
    ]

    severity_rows = [
        {"section": "severity", "label": severity, "value": qs.filter(severity=severity).count()}
        for severity in list(AlertSeverity.values)
    ]
    status_rows = [
        {"section": "status", "label": status, "value": qs.filter(status=status).count()}
        for status in list(AlertStatus.values)
    ]

    return ["section", "label", "value"], summary_rows + severity_rows + status_rows


def _notification_delivery_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    date_from, date_to = parse_report_date_range(parameters)
    validate_max_date_range(date_from, date_to, _get_report_config(job))

    qs = Notification.objects.select_related("organization", "recipient").filter(organization_id=job.organization_id)
    if job.data_center_id:
        alert_event_ids = list(
            AlertEvent.objects.filter(
                organization_id=job.organization_id,
                data_center_id=job.data_center_id,
            ).values_list("id", flat=True)
        )
        if alert_event_ids:
            qs = qs.filter(
                Q(metadata__alert_event_id__in=[str(alert_id) for alert_id in alert_event_ids])
                | Q(metadata__alert_id__in=[str(alert_id) for alert_id in alert_event_ids])
            )
        else:
            qs = qs.none()

    if date_from:
        qs = qs.filter(created_at__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__lte=date_to)

    recipient_id = parameters.get("recipient_id")
    if recipient_id not in (None, ""):
        try:
            recipient = User.objects.get(pk=recipient_id)
        except (User.DoesNotExist, ValueError, TypeError):
            raise ValueError("Invalid recipient_id.")
        qs = qs.filter(recipient_id=recipient.pk)

    channels = _normalize_choice_values(parameters.get("channel"), list(NotificationChannel.values), field_name="channel")
    if channels:
        qs = qs.filter(channel__in=channels)

    statuses = _normalize_choice_values(parameters.get("status"), list(NotificationStatus.values), field_name="status")
    if statuses:
        qs = qs.filter(status__in=statuses)

    alert_id = parameters.get("alert_id")
    if alert_id not in (None, ""):
        # Notification metadata has evolved over time; filter across the known alert-id keys best-effort.
        qs = qs.filter(Q(metadata__alert_event_id=str(alert_id)) | Q(metadata__alert_id=str(alert_id)))

    device_id = parameters.get("device_id")
    if device_id not in (None, ""):
        device = _resolve_device(job, device_id)
        qs = qs.filter(Q(metadata__device_id=str(device.pk)) | Q(metadata__device_id=str(device_id)))

    summary_rows = [
        {"section": "summary", "label": "total", "value": qs.count()},
        {"section": "summary", "label": "pending", "value": qs.filter(status=NotificationStatus.PENDING).count()},
        {"section": "summary", "label": "delivering", "value": qs.filter(status=NotificationStatus.DELIVERING).count()},
        {"section": "summary", "label": "sent", "value": qs.filter(status=NotificationStatus.SENT).count()},
        {"section": "summary", "label": "failed", "value": qs.filter(status=NotificationStatus.FAILED).count()},
        {"section": "summary", "label": "read_count", "value": qs.filter(read_at__isnull=False).count()},
    ]
    rows = summary_rows + [
        {"section": "status", "label": status, "value": qs.filter(status=status).count()}
        for status in list(NotificationStatus.values)
    ]
    rows.extend(
        {"section": "channel", "label": channel, "value": qs.filter(channel=channel).count()}
        for channel in list(NotificationChannel.values)
    )
    return ["section", "label", "value"], rows


def _device_inventory_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    qs = Device.objects.select_related(
        "organization",
        "data_center",
        "room",
        "rack",
        "device_type",
        "device_model__vendor",
    ).filter(organization_id=job.organization_id)

    data_center = _resolve_data_center_scope(job, parameters)
    if data_center:
        qs = qs.filter(data_center_id=data_center.pk)

    room_id = parameters.get("room_id")
    if room_id not in (None, ""):
        room = _resolve_room(job, room_id)
        if data_center and room.data_center_id != data_center.pk:
            raise ValueError("room_id must belong to the selected data center.")
        qs = qs.filter(room_id=room.pk)

    rack_id = parameters.get("rack_id")
    if rack_id not in (None, ""):
        rack = _resolve_rack(job, rack_id)
        if data_center and rack.data_center_id != data_center.pk:
            raise ValueError("rack_id must belong to the selected data center.")
        qs = qs.filter(rack_id=rack.pk)

    device_id = parameters.get("device_id")
    if device_id not in (None, ""):
        device = _resolve_device(job, device_id)
        if data_center and device.data_center_id != data_center.pk:
            raise ValueError("device_id must belong to the selected data center.")
        qs = qs.filter(pk=device.pk)

    device_model_id = parameters.get("device_model_id")
    if device_model_id not in (None, ""):
        device_model = _resolve_device_model(job, device_model_id)
        qs = qs.filter(device_model_id=device_model.pk)

    device_type_id = parameters.get("device_type_id")
    if device_type_id not in (None, ""):
        device_type = _resolve_device_type(job, device_type_id)
        qs = qs.filter(device_type_id=device_type.pk)

    statuses = _normalize_choice_values(parameters.get("status"), list(DeviceStatus.values), field_name="status")
    if statuses:
        qs = qs.filter(status__in=statuses)

    is_active = _normalize_bool(parameters.get("is_active"), field_name="is_active")
    if is_active is not None:
        qs = qs.filter(is_active=is_active)

    headers = [
        "device_id",
        "organization",
        "data_center",
        "room",
        "rack",
        "device",
        "code",
        "hostname",
        "ip_address",
        "device_type",
        "device_model",
        "vendor",
        "status",
        "is_active",
        "last_seen",
    ]
    rows = [
        {
            "device_id": str(device.id),
            "organization": getattr(device.organization, "name", None),
            "data_center": getattr(device.data_center, "name", None),
            "room": getattr(device.room, "name", None),
            "rack": getattr(device.rack, "name", None),
            "device": device.name,
            "code": device.code,
            "hostname": device.hostname,
            "ip_address": device.ip_address,
            "device_type": getattr(device.device_type, "name", None),
            "device_model": getattr(device.device_model, "name", None),
            "vendor": getattr(getattr(device.device_model, "vendor", None), "name", None),
            "status": device.status,
            "is_active": device.is_active,
            "last_seen": device.last_seen_at.isoformat() if device.last_seen_at else None,
        }
        for device in qs.order_by("name", "code")
    ]
    return headers, rows


def _user_label(user: User | None) -> str:
    if not user:
        return ""
    return getattr(user, "full_name", None) or getattr(user, "username", None) or getattr(user, "email", None) or str(user.pk)


def _alert_export_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    template_config = _get_report_config(job)
    date_from, date_to = parse_report_date_range(parameters, required=True)
    validate_max_date_range(date_from, date_to, template_config)

    qs = AlertEvent.objects.select_related(
        "organization",
        "data_center",
        "device",
        "device__device_model",
        "device__device_type",
        "device__room",
        "device__rack",
        "metric",
        "acknowledged_by",
        "resolved_by",
    ).filter(organization_id=job.organization_id)

    data_center = _resolve_data_center_scope(job, parameters)
    if data_center:
        qs = qs.filter(data_center_id=data_center.pk)
    if date_from:
        qs = qs.filter(triggered_at__gte=date_from)
    if date_to:
        qs = qs.filter(triggered_at__lte=date_to)

    device_id = parameters.get("device_id")
    if device_id not in (None, ""):
        device = _resolve_device(job, device_id)
        if data_center and device.data_center_id != data_center.pk:
            raise ValueError("device_id must belong to the selected data center.")
        qs = qs.filter(device_id=device.pk)

    device_model_id = parameters.get("device_model_id")
    if device_model_id not in (None, ""):
        device_model = _resolve_device_model(job, device_model_id)
        qs = qs.filter(device__device_model_id=device_model.pk)

    device_type_id = parameters.get("device_type_id")
    if device_type_id not in (None, ""):
        device_type = _resolve_device_type(job, device_type_id)
        qs = qs.filter(device__device_type_id=device_type.pk)

    room_id = parameters.get("room_id")
    if room_id not in (None, ""):
        room = _resolve_room(job, room_id)
        if data_center and room.data_center_id != data_center.pk:
            raise ValueError("room_id must belong to the selected data center.")
        qs = qs.filter(device__room_id=room.pk)

    rack_id = parameters.get("rack_id")
    if rack_id not in (None, ""):
        rack = _resolve_rack(job, rack_id)
        if data_center and rack.data_center_id != data_center.pk:
            raise ValueError("rack_id must belong to the selected data center.")
        qs = qs.filter(device__rack_id=rack.pk)

    metric_codes = _resolve_metric_codes(parameters)
    if metric_codes:
        qs = qs.filter(metric__code__in=metric_codes)

    severities = _normalize_choice_values(parameters.get("severity"), list(AlertSeverity.values), field_name="severity")
    if severities:
        qs = qs.filter(severity__in=severities)

    statuses = _normalize_choice_values(parameters.get("status"), list(AlertStatus.values), field_name="status")
    if statuses:
        qs = qs.filter(status__in=statuses)

    source_values = _normalize_list(parameters.get("source"))
    if source_values:
        # AlertEvent currently stores source in metadata, so we filter against that best-effort.
        qs = qs.filter(metadata__source__in=source_values)

    headers = [
        "triggered_at",
        "resolved_at",
        "organization",
        "data_center",
        "room",
        "rack",
        "device",
        "device_model",
        "metric",
        "severity",
        "status",
        "message",
        "occurrence_count",
        "acknowledged_by",
        "resolved_by",
    ]
    rows = []
    for alert in qs.order_by("triggered_at", "id"):
        rows.append(
            {
                "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
                "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
                "organization": getattr(alert.organization, "name", None),
                "data_center": getattr(alert.data_center, "name", None),
                "room": getattr(getattr(alert.device, "room", None), "name", None),
                "rack": getattr(getattr(alert.device, "rack", None), "name", None),
                "device": getattr(alert.device, "name", None),
                "device_model": getattr(getattr(alert.device, "device_model", None), "name", None),
                "metric": getattr(alert.metric, "code", None),
                "severity": alert.severity,
                "status": alert.status,
                "message": alert.message,
                "occurrence_count": alert.occurrence_count,
                "acknowledged_by": _user_label(alert.acknowledged_by),
                "resolved_by": _user_label(alert.resolved_by),
            }
        )
    return headers, rows


def _audit_export_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    template_config = _get_report_config(job)
    date_from, date_to = parse_report_date_range(parameters, required=True)
    validate_max_date_range(date_from, date_to, template_config)

    qs = AuditLog.objects.select_related("organization", "actor").filter(organization_id=job.organization_id)
    if date_from:
        qs = qs.filter(created_at__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__lte=date_to)

    actor_id = parameters.get("actor_id")
    if actor_id not in (None, ""):
        try:
            actor = User.objects.get(pk=actor_id)
        except (User.DoesNotExist, ValueError, TypeError):
            raise ValueError("Invalid actor_id.")
        qs = qs.filter(actor_id=actor.pk)

    actions = _normalize_choice_values(parameters.get("actions"), list(AuditAction.values), field_name="actions")
    if actions:
        qs = qs.filter(action__in=actions)

    resource_type = parameters.get("resource_type")
    if resource_type not in (None, ""):
        qs = qs.filter(resource_type=str(resource_type).strip())

    resource_id = parameters.get("resource_id")
    if resource_id not in (None, ""):
        qs = qs.filter(resource_id=str(resource_id).strip())

    ip_address = parameters.get("ip_address")
    if ip_address not in (None, ""):
        qs = qs.filter(ip_address=str(ip_address).strip())

    headers = [
        "created_at",
        "actor",
        "action",
        "resource_type",
        "resource_id",
        "organization",
        "message",
        "ip_address",
        "user_agent",
    ]
    rows = [
        {
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "actor": _user_label(log.actor),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "organization": getattr(log.organization, "name", None),
            "message": log.message,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
        }
        for log in qs.order_by("created_at", "id")
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


def _format_timestamp_for_csv(value):
    if not value:
        return None
    try:
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return value.isoformat(timespec="minutes")
        except Exception:
            return str(value)


def _telemetry_export_rows(job: ReportJob) -> tuple[list[str], list[dict]]:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    template_config = _get_report_config(job)
    date_from, date_to = parse_report_date_range(parameters, required=True)
    validate_max_date_range(date_from, date_to, template_config, default_days=31)

    _canonical_metric_codes, metrics = _resolve_telemetry_metrics(
        _resolve_metric_codes(parameters, required=True)
    )

    metric_ids = [metric.pk for metric in metrics]
    qs = TelemetryPoint.objects.select_related(
        "organization",
        "data_center",
        "device",
        "device__organization",
        "device__data_center",
        "device__room",
        "device__rack",
        "device__device_model__vendor",
        "device__device_model__device_type",
        "device__device_type",
        "metric",
    ).filter(organization_id=job.organization_id, metric_id__in=metric_ids)

    data_center = _resolve_data_center_scope(job, parameters)
    if data_center:
        qs = qs.filter(data_center_id=data_center.pk)

    if date_from:
        qs = qs.filter(time__gte=date_from)
    if date_to:
        qs = qs.filter(time__lte=date_to)

    device_id = parameters.get("device_id")
    if device_id not in (None, ""):
        device = _resolve_device(job, device_id)
        if data_center and device.data_center_id != data_center.pk:
            raise ValueError("device_id must belong to the selected data center.")
        qs = qs.filter(device_id=device.pk)

    device_model_id = parameters.get("device_model_id")
    if device_model_id not in (None, ""):
        device_model = _resolve_device_model(job, device_model_id)
        qs = qs.filter(device__device_model_id=device_model.pk)

    device_type_id = parameters.get("device_type_id")
    if device_type_id not in (None, ""):
        device_type = _resolve_device_type(job, device_type_id)
        qs = qs.filter(device__device_type_id=device_type.pk)

    room_id = parameters.get("room_id")
    if room_id not in (None, ""):
        room = _resolve_room(job, room_id)
        if data_center and room.data_center_id != data_center.pk:
            raise ValueError("room_id must belong to the selected data center.")
        qs = qs.filter(device__room_id=room.pk)

    rack_id = parameters.get("rack_id")
    if rack_id not in (None, ""):
        rack = _resolve_rack(job, rack_id)
        if data_center and rack.data_center_id != data_center.pk:
            raise ValueError("rack_id must belong to the selected data center.")
        qs = qs.filter(device__rack_id=rack.pk)

    headers = [
        "timestamp",
        "organization",
        "data_center",
        "room",
        "rack",
        "device",
        "device_model",
        "device_type",
        "metric_code",
        "metric_name",
        "value",
        "unit",
        "quality",
    ]

    def row_iterator():
        for point in qs.order_by("time", "device__name", "metric__code").iterator(chunk_size=2000):
            device = point.device
            metric = point.metric
            yield {
                "timestamp": _format_timestamp_for_csv(point.time),
                "organization": getattr(point.organization, "name", None),
                "data_center": getattr(point.data_center, "name", None),
                "room": getattr(device.room, "name", None) if getattr(device, "room", None) else None,
                "rack": getattr(device.rack, "name", None) if getattr(device, "rack", None) else None,
                "device": getattr(device, "name", None),
                "device_model": getattr(device.device_model, "name", None) if getattr(device, "device_model", None) else None,
                "device_type": getattr(device.device_type, "name", None) if getattr(device, "device_type", None) else None,
                "metric_code": getattr(metric, "code", None),
                "metric_name": getattr(metric, "name", None),
                "value": _telemetry_value(point),
                "unit": getattr(metric, "unit", None) or "",
                "quality": point.quality or "",
            }

    return headers, row_iterator()


def _build_report(job: ReportJob) -> tuple[str, bytes]:
    report_type = _resolve_report_type(job)
    _resolve_output_format(job)
    if report_type == "alert_summary":
        headers, rows = _alert_summary_rows(job)
    elif report_type == "notification_delivery":
        headers, rows = _notification_delivery_rows(job)
    elif report_type == "device_inventory":
        headers, rows = _device_inventory_rows(job)
    elif report_type == "alert_export":
        headers, rows = _alert_export_rows(job)
    elif report_type == "audit_export":
        headers, rows = _audit_export_rows(job)
    elif report_type == "telemetry_export":
        headers, rows = _telemetry_export_rows(job)
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
                message = _format_validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
                job.status = ReportJobStatus.FAILED
                job.completed_at = timezone.now()
                job.error_message = message
                job.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
                _safe_write_audit(
                    "REPORT_GENERATION_FAILED",
                    "ReportJob",
                    job.pk,
                    organization=job.organization,
                    actor=job.requested_by,
                    message=message,
                )
        return job
