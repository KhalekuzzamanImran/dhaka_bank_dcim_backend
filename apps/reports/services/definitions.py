from __future__ import annotations

from copy import deepcopy

from django.core.exceptions import ValidationError

from apps.notifications.models import NotificationChannel

from ..definition_seeds import REPORT_DEFINITION_SEEDS
from ..enums import ReportArtifactFormat
from ..models import ReportDefinition
from .validators import validate_parameter_schema

RESERVED_RUNTIME_PARAMETER_KEYS = {
    "output_format",
    "primary_format",
    "attachment_formats",
    "max_date_range_days",
    "schedule_id",
    "schedule_name",
    "delivery_time",
    "requested_format",
    "attach_raw_data",
    "trigger_source",
    "window_start",
    "window_end",
    "scheduled_for",
    "organization_id",
    "organization_name",
    "data_center_id",
    "data_center_name",
    "delivery_channels",
}


def seed_report_definitions(ReportDefinitionModel=None, *, seeds=None):
    if ReportDefinitionModel is None:
        from ..models import ReportDefinition as ReportDefinitionModel  # local import for runtime use

    definition_seeds = seeds or REPORT_DEFINITION_SEEDS
    created = 0
    updated = 0

    for seed in definition_seeds:
        defaults = deepcopy(seed)
        code = defaults.pop("code")
        defaults.setdefault("version", 1)
        obj, was_created = ReportDefinitionModel.objects.update_or_create(code=code, defaults=defaults)
        if was_created:
            created += 1
        else:
            updated += 1
            changed = False
            for field_name, value in defaults.items():
                if getattr(obj, field_name) != value:
                    setattr(obj, field_name, value)
                    changed = True
            if changed:
                obj.save()

    return {"created": created, "updated": updated}


def get_active_definition_by_code(code: str | None):
    if not code:
        return None
    normalized = str(code).strip()
    if not normalized:
        return None
    return ReportDefinition.objects.filter(code__iexact=normalized, is_active=True).first()


def get_definition_by_code(code: str | None):
    if not code:
        return None
    normalized = str(code).strip()
    if not normalized:
        return None
    return ReportDefinition.objects.filter(code__iexact=normalized).first()


def build_definition_capabilities(definition: ReportDefinition) -> dict:
    return {
        "code": definition.code,
        "name": definition.name,
        "category": definition.category,
        "generator_key": definition.generator_key,
        "supported_formats": list(definition.supported_formats or []),
        "supported_delivery_channels": list(definition.supported_delivery_channels or []),
        "requires_telemetry": definition.requires_telemetry,
        "requires_data_center": definition.requires_data_center,
        "is_active": definition.is_active,
        "version": definition.version,
        "parameter_schema": deepcopy(definition.parameter_schema or {}),
    }


def _normalize_format_list(values):
    normalized = []
    seen = set()
    for value in values or []:
        candidate = str(value).strip().upper()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def validate_definition_request(
    definition: ReportDefinition,
    organization,
    data_center,
    parameters,
    primary_format,
    attachment_formats,
    delivery_channels,
):
    if not definition:
        raise ValidationError({"definition": "A report definition is required."})
    if not definition.is_active:
        raise ValidationError({"definition": "Selected report definition is inactive."})

    if definition.requires_data_center and not data_center:
        raise ValidationError({"data_center": "This report definition requires a data center."})
    if data_center and organization and data_center.organization_id != organization.id:
        raise ValidationError({"data_center": "Data center must belong to the selected organization."})

    schema_parameters = {
        key: value
        for key, value in (parameters or {}).items()
        if key not in RESERVED_RUNTIME_PARAMETER_KEYS
    }
    normalized_parameters = validate_parameter_schema(schema_parameters, definition.parameter_schema or {}, field_name="parameters")

    allowed_formats = {str(value).strip().upper() for value in definition.supported_formats or [] if str(value).strip()}
    if primary_format:
        normalized_primary_format = str(primary_format).strip().upper()
        if allowed_formats and normalized_primary_format not in allowed_formats:
            raise ValidationError({"primary_format": f"Unsupported format for this report definition: {normalized_primary_format}."})
    else:
        normalized_primary_format = None

    normalized_attachment_formats = _normalize_format_list(attachment_formats)
    unsupported_attachment_formats = [value for value in normalized_attachment_formats if allowed_formats and value not in allowed_formats]
    if unsupported_attachment_formats:
        raise ValidationError(
            {"attachment_formats": f"Unsupported attachment format(s): {', '.join(unsupported_attachment_formats)}."}
        )

    allowed_channels = {str(value).strip().upper() for value in definition.supported_delivery_channels or [] if str(value).strip()}
    normalized_delivery_channels = []
    for channel in delivery_channels or []:
        candidate = str(channel).strip().upper()
        if not candidate:
            continue
        if allowed_channels and candidate not in allowed_channels:
            raise ValidationError({"delivery_channels": f"Unsupported delivery channel: {candidate}."})
        if candidate not in normalized_delivery_channels:
            normalized_delivery_channels.append(candidate)

    if definition.supported_formats:
        unsupported_formats = [value for value in normalized_attachment_formats + ([normalized_primary_format] if normalized_primary_format else []) if value and value not in allowed_formats]
        if unsupported_formats:
            raise ValidationError({"primary_format": f"Unsupported format(s): {', '.join(sorted(set(unsupported_formats)))}."})

    return {
        "definition": definition,
        "parameters": normalized_parameters,
        "primary_format": normalized_primary_format,
        "attachment_formats": normalized_attachment_formats,
        "delivery_channels": normalized_delivery_channels,
    }
