from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.reports.constants import normalize_report_format, normalize_report_type


def normalize_report_parameters(parameters):
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise ValidationError({"parameters": "Parameters must be a dictionary/object."})
    normalized = dict(parameters)
    report_type = normalize_report_type(normalized.get("report_type"))
    if report_type:
        normalized["report_type"] = report_type
    output_format = normalize_report_format(normalized.get("output_format") or normalized.get("primary_format"))
    if output_format:
        normalized["output_format"] = output_format
    return normalized
