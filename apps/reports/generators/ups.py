from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_telemetry_metrics
from .registry import register_generator
from ..services.telemetry_history import fetch_telemetry_report_rows


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
    parameters = context.parameters or {}
    metric_codes = normalize_telemetry_metrics(parameters.get("metric_codes") or parameters.get("metrics") or UPS_DEFAULT_METRICS)
    rows, metrics = fetch_telemetry_report_rows(
        organization=context.organization,
        data_center=context.data_center,
        metric_codes=metric_codes,
        parameters=parameters,
    )
    return rows, metrics


@register_generator("ups_performance")
class UPSPerformanceGenerator(BaseReportGenerator):
    definition_code = "UPS_PERFORMANCE"
    generator_key = "ups_performance"
    supported_formats = ("CSV", "XLSX", "PDF")
    row_limit_for_pdf = 400

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        rows, metrics = _load_points(context)
        grouped = defaultdict(list)
        for row in rows:
            grouped[row.get("metric_code")].append(row)

        summary_rows = []
        detail_rows = []
        for metric in metrics:
            metric_points = grouped.get(metric.code, [])
            numeric_values = []
            last_value = None
            for point in metric_points:
                value = point.get("value")
                if value is not None:
                    if isinstance(value, bool):
                        numeric_values.append(1.0 if value else 0.0)
                    else:
                        numeric_values.append(float(value))
                    last_value = value
                detail_rows.append(
                    {
                        "timestamp": point.get("timestamp"),
                        "organization": point.get("organization"),
                        "data_center": point.get("data_center"),
                        "device": point.get("device"),
                        "metric_code": metric.code,
                        "metric_name": metric.name,
                        "value": value,
                        "unit": metric.unit,
                        "quality": point.get("quality"),
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
