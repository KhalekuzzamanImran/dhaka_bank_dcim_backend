from __future__ import annotations

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_telemetry_metrics
from .registry import register_generator
from ..services.telemetry_history import fetch_telemetry_report_rows


@register_generator("room_environment")
class EnvironmentalTrendGenerator(BaseReportGenerator):
    definition_code = "ENVIRONMENTAL_TREND"
    generator_key = "room_environment"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        parameters = context.parameters or {}
        metric_codes = normalize_telemetry_metrics(parameters.get("metrics") or context.template_snapshot.get("configuration", {}).get("metrics"))
        if not metric_codes:
            metric_codes = ["room_temperature", "room_humidity", "pac_room_temperature", "pac_room_humidity"]
        rows, _ = fetch_telemetry_report_rows(
            organization=context.organization,
            data_center=context.data_center,
            metric_codes=metric_codes,
            parameters=parameters,
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
