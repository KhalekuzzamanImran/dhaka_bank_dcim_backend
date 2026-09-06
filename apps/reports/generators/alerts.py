from __future__ import annotations

from datetime import datetime, time
from collections import Counter

from django.utils.text import slugify
from django.utils import timezone

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_list, parse_date_range
from .registry import register_generator


def _alert_rows(context: GeneratorContext):
    from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus

    parameters = context.parameters or {}
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
    ).filter(organization_id=context.organization.id)

    if context.data_center:
        qs = qs.filter(data_center_id=context.data_center.id)
    start_dt, end_dt = parse_date_range(parameters)
    if context.definition.code == "ALERT_SUMMARY" and start_dt is None and end_dt is None:
        current_day = timezone.localdate(context.generated_at)
        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime.combine(current_day, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(current_day, time.max), tz)
    if start_dt:
        qs = qs.filter(triggered_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(triggered_at__lte=end_dt)
    if parameters.get("severity"):
        severities = [value.upper() for value in normalize_list(parameters.get("severity")) if value]
        if severities:
            qs = qs.filter(severity__in=severities)
    if parameters.get("status"):
        statuses = [value.upper() for value in normalize_list(parameters.get("status")) if value]
        if statuses:
            qs = qs.filter(status__in=statuses)
    device_ids = parameters.get("device_ids")
    if device_ids:
        if isinstance(device_ids, list):
            valid_ids = [d for d in device_ids if d]
            if valid_ids:
                qs = qs.filter(device_id__in=valid_ids)
        elif isinstance(device_ids, str):
            qs = qs.filter(device_id=device_ids)
    elif parameters.get("device_id"):
        qs = qs.filter(device_id=parameters["device_id"])

    return qs


def _alert_detail_table_rows(qs):
    return (
        {
            "triggered_at": alert.triggered_at,
            "resolved_at": alert.resolved_at,
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
            "acknowledged_by": getattr(alert.acknowledged_by, "username", None),
            "resolved_by": getattr(alert.resolved_by, "username", None),
        }
        for alert in qs.order_by("triggered_at", "id").iterator(chunk_size=1000)
    )


@register_generator("alert_summary")
class AlertSummaryGenerator(BaseReportGenerator):
    definition_code = "ALERT_SUMMARY"
    generator_key = "alert_summary"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        qs = _alert_rows(context)
        return ReportDataset(
            title="Daily alert summary",
            subtitle="Detailed alert events for today",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Alerts",
                    columns=["triggered_at", "resolved_at", "organization", "data_center", "room", "rack", "device", "device_model", "metric", "severity", "status", "message", "occurrence_count", "acknowledged_by", "resolved_by"],
                    rows=_alert_detail_table_rows(qs),
                    primary=True,
                )
            ],
        )


@register_generator("alert_export")
class AlertDetailGenerator(BaseReportGenerator):
    definition_code = "ALERT_DETAIL"
    generator_key = "alert_export"
    supported_formats = ("CSV", "XLSX", "PDF")

    def get_filename(self, context: GeneratorContext, output_format: str) -> str:
        prefix = slugify(context.definition.name or context.definition.code or "report") or "report"
        gen_time = getattr(context, "local_generated_at", None) or getattr(context, "generated_at", None)
        timestamp = gen_time.strftime("%Y%m%d_%H%M%S") if gen_time else context.generated_at.strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{context.job.pk}_{timestamp}.{output_format.lower()}"

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        qs = _alert_rows(context)
        return ReportDataset(
            title="Alert Summary",
            subtitle="Detailed alert events",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Alerts",
                    columns=["triggered_at", "resolved_at", "organization", "data_center", "room", "rack", "device", "device_model", "metric", "severity", "status", "message", "occurrence_count", "acknowledged_by", "resolved_by"],
                    rows=_alert_detail_table_rows(qs),
                    primary=True,
                )
            ],
        )
