from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import normalize_telemetry_metric_codes, parse_date_range
from .registry import register_generator


def _telemetry_rows(context: GeneratorContext):
    from apps.telemetry.models import MetricDefinition, TelemetryPoint

    parameters = context.parameters or {}
    metric_codes = normalize_telemetry_metric_codes(parameters.get("metric_codes") or context.template_snapshot.get("default_parameters", {}).get("metric_codes"))
    if not metric_codes:
        metric_codes = normalize_telemetry_metric_codes(context.template_snapshot.get("configuration", {}).get("default_metric_codes"))
    metrics = MetricDefinition.objects.filter(code__in=metric_codes, is_active=True)
    metric_ids = list(metrics.values_list("id", flat=True))
    qs = TelemetryPoint.objects.select_related(
        "organization",
        "data_center",
        "device",
        "device__room",
        "device__rack",
        "device__device_model",
        "device__device_type",
        "metric",
    ).filter(organization_id=context.organization.id, metric_id__in=metric_ids)

    if context.data_center:
        qs = qs.filter(data_center_id=context.data_center.id)
    start_dt, end_dt = parse_date_range(parameters, required=True)
    if start_dt:
        qs = qs.filter(time__gte=start_dt)
    if end_dt:
        qs = qs.filter(time__lte=end_dt)
    if parameters.get("device_id"):
        qs = qs.filter(device_id=parameters["device_id"])
    if parameters.get("room_id"):
        qs = qs.filter(device__room_id=parameters["room_id"])
    if parameters.get("rack_id"):
        qs = qs.filter(device__rack_id=parameters["rack_id"])
    if parameters.get("device_type_id"):
        qs = qs.filter(device__device_type_id=parameters["device_type_id"])
    return qs, list(metrics)


def _value_for_point(point):
    if point.value_float is not None:
        return point.value_float
    if point.value_integer is not None:
        return point.value_integer
    if point.value_boolean is not None:
        return point.value_boolean
    if point.value_text is not None:
        return point.value_text
    return point.raw_value_text


@register_generator("telemetry_export")
class TelemetryExportGenerator(BaseReportGenerator):
    definition_code = "TELEMETRY_EXPORT"
    generator_key = "telemetry_export"
    supported_formats = ("CSV", "XLSX", "PDF")
    row_limit_for_pdf = 500

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        qs, metrics = _telemetry_rows(context)
        metric_names = {metric.code: metric.name for metric in metrics}
        rows = (
            {
                "timestamp": point.time,
                "organization": getattr(point.organization, "name", None),
                "data_center": getattr(point.data_center, "name", None),
                "room": getattr(getattr(point.device, "room", None), "name", None),
                "rack": getattr(getattr(point.device, "rack", None), "name", None),
                "device": getattr(point.device, "name", None),
                "device_model": getattr(getattr(point.device, "device_model", None), "name", None),
                "device_type": getattr(getattr(point.device, "device_type", None), "name", None),
                "metric_code": getattr(point.metric, "code", None),
                "metric_name": metric_names.get(getattr(point.metric, "code", None), getattr(point.metric, "name", None)),
                "value": _value_for_point(point),
                "unit": getattr(point.metric, "unit", None),
                "quality": point.quality,
            }
            for point in qs.order_by("time", "device__name", "metric__code").iterator(chunk_size=5000)
        )
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
