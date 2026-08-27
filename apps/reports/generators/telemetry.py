from __future__ import annotations

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_telemetry_metric_codes, parse_date_range
from .registry import register_generator
from ..services.telemetry_history import fetch_telemetry_report_rows


def _telemetry_rows(context: GeneratorContext):
    parameters = context.parameters or {}
    parse_date_range(parameters, required=True)
    metric_codes = normalize_telemetry_metric_codes(parameters.get("metric_codes") or context.template_snapshot.get("default_parameters", {}).get("metric_codes"))
    if not metric_codes:
        metric_codes = normalize_telemetry_metric_codes(context.template_snapshot.get("configuration", {}).get("default_metric_codes"))
    rows, metrics = fetch_telemetry_report_rows(
        organization=context.organization,
        data_center=context.data_center,
        metric_codes=metric_codes,
        parameters=parameters,
    )
    return rows, metrics


@register_generator("telemetry_export")
class TelemetryExportGenerator(BaseReportGenerator):
    definition_code = "TELEMETRY_EXPORT"
    generator_key = "telemetry_export"
    supported_formats = ("CSV", "XLSX", "PDF")
    # Telemetry exports can be legitimately large; PDF should paginate instead
    # of hard-failing and the UI already warns when CSV/XLSX is a better fit.
    row_limit_for_pdf = 0

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        rows, metrics = _telemetry_rows(context)
        columns = [
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
        return ReportDataset(
            title="Telemetry Export",
            subtitle="Historical telemetry export",
            metadata={"report_type": context.definition.code},
            tables=[ReportTable(name="Telemetry", columns=columns, rows=rows, primary=True)],
        )
