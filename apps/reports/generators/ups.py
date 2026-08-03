from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_telemetry_metrics, parse_date_range
from .registry import register_generator


UPS_DEFAULT_METRICS = [
    "ups_load_percent",
    "ups_battery_charge",
    "ups_battery_capacity_percent",
    "ups_battery_runtime_remaining",
    "ups_input_voltage",
    "ups_output_voltage",
    "ups_output_frequency",
    "ups_output_current",
    "ups_battery_status",
    "ups_comm_status",
]


def _load_points(context: GeneratorContext):
    from apps.telemetry.models import MetricDefinition, TelemetryPoint

    parameters = context.parameters or {}
    metric_codes = normalize_telemetry_metrics(parameters.get("metric_codes") or parameters.get("metrics") or UPS_DEFAULT_METRICS)
    metrics = MetricDefinition.objects.filter(code__in=metric_codes, is_active=True)
    metric_ids = list(metrics.values_list("id", flat=True))
    qs = TelemetryPoint.objects.select_related("organization", "data_center", "device", "metric").filter(
        organization_id=context.organization.id,
        metric_id__in=metric_ids,
    )
    if context.data_center:
        qs = qs.filter(data_center_id=context.data_center.id)
    start_dt, end_dt = parse_date_range(parameters)
    if start_dt:
        qs = qs.filter(time__gte=start_dt)
    if end_dt:
        qs = qs.filter(time__lte=end_dt)
    if parameters.get("device_id"):
        qs = qs.filter(device_id=parameters["device_id"])
    return qs, list(metrics)


@register_generator("ups_performance")
class UPSPerformanceGenerator(BaseReportGenerator):
    definition_code = "UPS_PERFORMANCE"
    generator_key = "ups_performance"
    supported_formats = ("CSV", "XLSX", "PDF")
    row_limit_for_pdf = 400

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        qs, metrics = _load_points(context)
        grouped = defaultdict(list)
        for point in qs.order_by("time", "metric__code").iterator(chunk_size=5000):
            grouped[getattr(point.metric, "code", None)].append(point)

        summary_rows = []
        detail_rows = []
        for metric in metrics:
            metric_points = grouped.get(metric.code, [])
            numeric_values = []
            last_value = None
            for point in metric_points:
                value = point.value_float if point.value_float is not None else point.value_integer
                if value is not None:
                    numeric_values.append(float(value))
                    last_value = value
                detail_rows.append(
                    {
                        "timestamp": point.time,
                        "organization": getattr(point.organization, "name", None),
                        "data_center": getattr(point.data_center, "name", None),
                        "device": getattr(point.device, "name", None),
                        "metric_code": metric.code,
                        "metric_name": metric.name,
                        "value": value if value is not None else point.value_text or point.raw_value_text,
                        "unit": metric.unit,
                        "quality": point.quality,
                    }
                )
            if numeric_values:
                summary_rows.append({"label": f"{metric.code}_min", "value": min(numeric_values)})
                summary_rows.append({"label": f"{metric.code}_max", "value": max(numeric_values)})
                summary_rows.append({"label": f"{metric.code}_avg", "value": round(mean(numeric_values), 3)})
                summary_rows.append({"label": f"{metric.code}_last", "value": last_value})

        return ReportDataset(
            title="UPS Performance",
            subtitle="UPS telemetry summary",
            metadata={"report_type": context.definition.code},
            summary_rows=summary_rows,
            tables=[
                ReportTable(
                    name="UPS Metrics",
                    columns=["timestamp", "organization", "data_center", "device", "metric_code", "metric_name", "value", "unit", "quality"],
                    rows=detail_rows,
                    primary=True,
                )
            ],
        )

