from __future__ import annotations

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_telemetry_metrics, parse_date_range
from .registry import register_generator


@register_generator("room_environment")
class EnvironmentalTrendGenerator(BaseReportGenerator):
    definition_code = "ENVIRONMENTAL_TREND"
    generator_key = "room_environment"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        from apps.telemetry.models import MetricDefinition, TelemetryPoint

        parameters = context.parameters or {}
        metric_codes = normalize_telemetry_metrics(parameters.get("metrics") or context.template_snapshot.get("configuration", {}).get("metrics"))
        if not metric_codes:
            metric_codes = ["room_temperature", "room_humidity", "pac_room_temperature", "pac_room_humidity"]
        metrics = MetricDefinition.objects.filter(code__in=metric_codes, is_active=True)
        metric_ids = list(metrics.values_list("id", flat=True))
        qs = TelemetryPoint.objects.select_related(
            "organization",
            "data_center",
            "device",
            "device__room",
            "metric",
        ).filter(organization_id=context.organization.id, metric_id__in=metric_ids)
        if context.data_center:
            qs = qs.filter(data_center_id=context.data_center.id)
        start_dt, end_dt = parse_date_range(parameters)
        if start_dt:
            qs = qs.filter(time__gte=start_dt)
        if end_dt:
            qs = qs.filter(time__lte=end_dt)

        rows = (
            {
                "timestamp": point.time,
                "room_name": getattr(getattr(point.device, "room", None), "name", None),
                "room_code": getattr(getattr(point.device, "room", None), "code", None),
                "device_name": getattr(point.device, "name", None),
                "device_code": getattr(point.device, "code", None),
                "metric_code": getattr(point.metric, "code", None),
                "metric_name": getattr(point.metric, "name", None),
                "value": point.value_float if point.value_float is not None else point.value_integer or point.value_text or point.raw_value_text,
                "unit": getattr(point.metric, "unit", None),
                "quality": point.quality,
                "source": point.source,
            }
            for point in qs.order_by("time", "device__name", "metric__code").iterator(chunk_size=5000)
        )
        return ReportDataset(
            title="Environmental Trend",
            subtitle="Environmental telemetry trend",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Environment",
                    columns=["timestamp", "room_name", "room_code", "device_name", "device_code", "metric_code", "metric_name", "value", "unit", "quality", "source"],
                    rows=rows,
                    primary=True,
                )
            ],
        )
