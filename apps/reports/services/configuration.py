from __future__ import annotations

from copy import deepcopy

from django.core.exceptions import ValidationError

from apps.alerts.models import AlertSeverity, AlertStatus
from apps.audit.models import AuditAction
from apps.devices.models import DeviceStatus, DeviceType
from apps.notifications.models import NotificationChannel, NotificationStatus
from apps.telemetry.models import MetricDefinition

from ..constants import normalize_key


SUPPORTED_TEMPLATE_OUTPUT_FORMATS = ["csv", "xlsx", "pdf"]


REPORT_TEMPLATE_OPTIONS = {
    "ALERT_SUMMARY": {
        "available_columns": ["section", "label", "value"],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "date_from",
            "date_to",
            "data_center_id",
            "device_id",
            "device_model_id",
            "device_type_id",
            "room_id",
            "rack_id",
            "metric_codes",
            "severity",
            "status",
        ],
        "field_options": {
            "severity": list(AlertSeverity.values),
            "status": list(AlertStatus.values),
        },
        "maximum_date_range_days": 90,
    },
    "NOTIFICATION_DELIVERY": {
        "available_columns": [
            "section",
            "label",
            "value",
            "notification_id",
            "subject",
            "recipient",
            "recipient_address",
            "channel",
            "status",
            "queued_at",
            "sent_at",
            "failed_at",
            "error_message",
        ],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "date_from",
            "date_to",
            "recipient_id",
            "channel",
            "status",
            "alert_id",
            "device_id",
        ],
        "field_options": {
            "channel": list(NotificationChannel.values),
            "status": list(NotificationStatus.values),
        },
        "maximum_date_range_days": 90,
    },
    "DEVICE_INVENTORY": {
        "available_columns": [
            "organization",
            "data_center",
            "room",
            "rack",
            "device",
            "code",
            "hostname",
            "ip_address",
            "device_type",
            "device_model",
            "vendor",
            "status",
            "is_active",
            "last_seen",
        ],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "data_center_id",
            "room_id",
            "rack_id",
            "device_id",
            "device_model_id",
            "device_type_id",
            "status",
            "is_active",
        ],
        "field_options": {
            "status": list(DeviceStatus.values),
        },
        "maximum_date_range_days": None,
    },
    "ALERT_DETAIL": {
        "available_columns": [
            "triggered_at",
            "resolved_at",
            "organization",
            "data_center",
            "room",
            "rack",
            "device",
            "device_model",
            "metric",
            "severity",
            "status",
            "message",
            "occurrence_count",
            "acknowledged_by",
            "resolved_by",
        ],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "date_from",
            "date_to",
            "device_id",
            "device_model_id",
            "device_type_id",
            "data_center_id",
            "room_id",
            "rack_id",
            "metric_codes",
            "severity",
            "status",
            "source",
        ],
        "field_options": {
            "severity": list(AlertSeverity.values),
            "status": list(AlertStatus.values),
        },
        "maximum_date_range_days": 90,
    },
    "AUDIT_EXPORT": {
        "available_columns": [
            "created_at",
            "actor",
            "action",
            "resource_type",
            "resource_id",
            "organization",
            "message",
            "ip_address",
            "user_agent",
        ],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "date_from",
            "date_to",
            "actor_id",
            "actions",
            "resource_type",
            "resource_id",
            "ip_address",
        ],
        "field_options": {
            "actions": list(AuditAction.values),
        },
        "maximum_date_range_days": 90,
    },
    "TELEMETRY_EXPORT": {
        "available_columns": [
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
        ],
        "required_fields": ["default_metric_codes"],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "default_date_range",
            "max_date_range_days",
            "aggregation",
            "quality_filter",
            "device_id",
            "device_model_id",
            "device_type_id",
            "data_center_id",
            "room_id",
            "rack_id",
        ],
        "field_options": {
            "aggregation": ["raw", "1_minute", "5_minutes", "15_minutes", "hourly", "daily"],
        },
        "maximum_date_range_days": 31,
    },
    "ENVIRONMENTAL_TREND": {
        "available_columns": [
            "timestamp",
            "room_name",
            "room_code",
            "device_name",
            "device_code",
            "metric_code",
            "metric_name",
            "value",
            "unit",
            "quality",
            "source",
        ],
        "optional_fields": [
            "default_columns",
            "default_parameters",
            "default_date_range",
            "max_date_range_days",
            "metrics",
            "aggregation",
            "device_id",
            "device_model_id",
            "device_type_id",
            "data_center_id",
            "room_id",
            "rack_id",
        ],
        "field_options": {
            "aggregation": ["raw", "1_minute", "5_minutes", "15_minutes", "hourly", "daily"],
        },
        "maximum_date_range_days": 90,
    },
}


def _as_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = str(value).split(",")
    result: list[str] = []
    seen = set()
    for entry in values:
        item = str(entry).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _as_dict(value, *, field_name: str) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValidationError({field_name: "Must be a dictionary/object."})
    return dict(value)


def _normalize_columns(value, *, field_name: str) -> list[str]:
    columns = _as_list(value)
    if not columns:
        return []
    normalized: list[str] = []
    for column in columns:
        if column not in normalized:
            normalized.append(column)
    return normalized


def _normalize_metric_codes(value, *, field_name: str) -> list[str]:
    codes = _as_list(value)
    if not codes:
        return []
    active_codes = set(MetricDefinition.objects.filter(is_active=True, code__in=codes).values_list("code", flat=True))
    missing = [code for code in codes if code not in active_codes]
    if missing:
        raise ValidationError({field_name: f"Unknown or inactive metric code(s): {', '.join(missing)}."})
    normalized: list[str] = []
    for code in codes:
        if code not in active_codes or code in normalized:
            continue
        normalized.append(code)
    return normalized


def _normalize_positive_int(value, *, field_name: str, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: "Must be a positive integer."})
    if parsed <= 0:
        raise ValidationError({field_name: "Must be greater than zero."})
    return parsed


def validate_report_template_config(config, *, existing_config: dict | None = None, definition_code: str | None = None) -> dict:
    if not isinstance(config, dict):
        raise ValidationError({"config": "Config must be a dictionary/object."})

    normalized = deepcopy(existing_config or {})
    normalized.update(config)
    output_format = str(normalized.get("output_format", "csv")).strip()
    normalized_output_format = normalize_key(output_format).upper()
    if normalized_output_format not in {"CSV", "XLSX", "PDF"}:
        raise ValidationError({"output_format": "Unsupported output format. Supported formats are CSV, XLSX, and PDF."})
    normalized["output_format"] = normalized_output_format.lower()

    allowed_output_formats = _as_list(normalized.get("allowed_output_formats"))
    normalized_allowed_output_formats = [normalize_key(value) for value in allowed_output_formats if normalize_key(value)]
    if normalized_allowed_output_formats and any(value.upper() not in {"CSV", "XLSX", "PDF"} for value in normalized_allowed_output_formats):
        raise ValidationError({
            "allowed_output_formats": "Unsupported output format. Supported formats are CSV, XLSX, and PDF.",
        })
    normalized["allowed_output_formats"] = [value.lower() for value in normalized_allowed_output_formats] or [normalized["output_format"]]

    resolved_definition_code = str(definition_code or "").strip().upper()
    options = REPORT_TEMPLATE_OPTIONS.get(resolved_definition_code, {})
    if "default_columns" in normalized:
        normalized["default_columns"] = _normalize_columns(
            normalized.get("default_columns"),
            field_name="default_columns",
        )

    if "default_parameters" in normalized:
        normalized["default_parameters"] = _as_dict(normalized.get("default_parameters"), field_name="default_parameters")

    if "required_filters" in normalized:
        normalized["required_filters"] = _as_list(normalized.get("required_filters"))

    if "optional_filters" in normalized:
        normalized["optional_filters"] = _as_list(normalized.get("optional_filters"))

    if "default_metric_codes" in normalized:
        normalized["default_metric_codes"] = _normalize_metric_codes(
            normalized.get("default_metric_codes"),
            field_name="default_metric_codes",
        )

    if resolved_definition_code == "TELEMETRY_EXPORT" and isinstance(normalized.get("default_parameters"), dict):
        default_parameters = dict(normalized["default_parameters"])
        if "metric_codes" in default_parameters:
            default_parameters["metric_codes"] = _normalize_metric_codes(
                default_parameters.get("metric_codes"),
                field_name="default_parameters.metric_codes",
            )
        normalized["default_parameters"] = default_parameters

    if "max_date_range_days" in normalized:
        normalized["max_date_range_days"] = _normalize_positive_int(
            normalized.get("max_date_range_days"),
            field_name="max_date_range_days",
        )

    if "default_date_range" in normalized and normalized["default_date_range"] not in (None, ""):
        default_date_range = normalized["default_date_range"]
        if not isinstance(default_date_range, dict):
            raise ValidationError({"default_date_range": "Must be a dictionary/object."})
        normalized["default_date_range"] = dict(default_date_range)

    return normalized


def build_report_template_options(template) -> dict:
    definition_code = str(getattr(getattr(template, "definition", None), "code", None) or "").strip().upper()
    if not definition_code:
        raise ValidationError({"definition": "Unsupported report definition."})

    template_config = getattr(template, "config", None)
    template_config = template_config if isinstance(template_config, dict) else {}
    options = REPORT_TEMPLATE_OPTIONS.get(definition_code, {})
    definition_supported_formats = []
    if getattr(template, "definition_id", None) and getattr(template, "definition", None):
        definition_supported_formats = [
            str(value).strip().lower()
            for value in getattr(template.definition, "supported_formats", []) or []
            if str(value).strip()
        ]
    maximum_date_range_days = template_config.get("max_date_range_days", options.get("maximum_date_range_days"))
    try:
        maximum_date_range_days = int(maximum_date_range_days) if maximum_date_range_days not in (None, "") else None
    except (TypeError, ValueError):
        maximum_date_range_days = options.get("maximum_date_range_days")

    response = {
        "definition_code": definition_code,
        "supported_output_formats": definition_supported_formats or list(SUPPORTED_TEMPLATE_OUTPUT_FORMATS),
        "available_columns": list(options.get("available_columns", [])),
        "required_fields": list(options.get("required_fields", [])),
        "optional_fields": list(options.get("optional_fields", [])),
        "aggregation_options": list(options.get("field_options", {}).get("aggregation", [])),
        "maximum_date_range_days": maximum_date_range_days,
    }

    field_options = {}
    for field_name, values in options.get("field_options", {}).items():
        field_options[field_name] = list(values)

    if definition_code == "DEVICE_INVENTORY":
        field_options["device_type_ids"] = [
            {
                "id": str(device_type.pk),
                "code": device_type.code,
                "name": device_type.name,
                "is_active": getattr(device_type, "is_active", True),
            }
            for device_type in DeviceType.objects.all().order_by("name")
        ]

    response["field_options"] = field_options
    return response
