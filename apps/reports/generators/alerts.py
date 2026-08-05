from __future__ import annotations

from collections import Counter, defaultdict

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import parse_date_range
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
    if start_dt:
        qs = qs.filter(triggered_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(triggered_at__lte=end_dt)
    if parameters.get("severity"):
        qs = qs.filter(severity=parameters["severity"])
    if parameters.get("status"):
        qs = qs.filter(status=parameters["status"])
    if parameters.get("device_id"):
        qs = qs.filter(device_id=parameters["device_id"])

    return qs


@register_generator("alert_summary")
class AlertSummaryGenerator(BaseReportGenerator):
    definition_code = "ALERT_SUMMARY"
    generator_key = "alert_summary"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        from apps.alerts.models import AlertSeverity, AlertStatus

        qs = _alert_rows(context)
        total = qs.count()
        severity_counts = Counter(qs.values_list("severity", flat=True))
        status_counts = Counter(qs.values_list("status", flat=True))
        rows = [
            {"section": "summary", "label": "total_alerts", "value": total},
            {"section": "summary", "label": "open_total", "value": qs.filter(status=AlertStatus.OPEN).count()},
            {"section": "summary", "label": "acknowledged_total", "value": qs.filter(status=AlertStatus.ACKNOWLEDGED).count()},
            {"section": "summary", "label": "resolved_total", "value": qs.filter(status=AlertStatus.RESOLVED).count()},
        ]
        for severity in AlertSeverity.values:
            rows.append({"section": "severity", "label": severity, "value": severity_counts.get(severity, 0)})
        for status in AlertStatus.values:
            rows.append({"section": "status", "label": status, "value": status_counts.get(status, 0)})
        return ReportDataset(
            title="Daily alert summary",
            subtitle="Aggregated alert status",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Summary",
                    columns=["section", "label", "value"],
                    rows=rows,
                    primary=True,
                )
            ],
        )


@register_generator("alert_export")
class AlertDetailGenerator(BaseReportGenerator):
    definition_code = "ALERT_DETAIL"
    generator_key = "alert_export"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        qs = _alert_rows(context)
        rows = (
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
        return ReportDataset(
            title="Alert Detail",
            subtitle="Detailed alert events",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Alerts",
                    columns=["triggered_at", "resolved_at", "organization", "data_center", "room", "rack", "device", "device_model", "metric", "severity", "status", "message", "occurrence_count", "acknowledged_by", "resolved_by"],
                    rows=rows,
                    primary=True,
                )
            ],
        )
