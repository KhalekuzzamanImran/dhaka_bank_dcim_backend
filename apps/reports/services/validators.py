from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def _is_blank(value) -> bool:
    return value in (None, "")


def _collect_error(errors: dict[str, list[str]], field_name: str, message: str):
    errors.setdefault(field_name, []).append(message)


def _normalize_required_types(schema: dict) -> list[str]:
    value = schema.get("type")
    if value is None:
        if "properties" in schema:
            return ["object"]
        if "items" in schema:
            return ["array"]
        return ["string"]
    if isinstance(value, list):
        return [str(entry).strip().lower() for entry in value if str(entry).strip()]
    return [str(value).strip().lower()]


def _allowed_null(schema: dict) -> bool:
    if schema.get("nullable") is True:
        return True
    return "null" in _normalize_required_types(schema)


def _normalize_bool(value, *, field_name: str):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    raise ValidationError({field_name: "Must be a boolean value."})


def _normalize_int(value, *, field_name: str):
    if isinstance(value, bool):
        raise ValidationError({field_name: "Must be an integer value."})
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: "Must be an integer value."})


def _normalize_float(value, *, field_name: str):
    if isinstance(value, bool):
        raise ValidationError({field_name: "Must be a number."})
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: "Must be a number."})


def _normalize_uuid(value, *, field_name: str):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError({field_name: "Must be a valid UUID."})


def _normalize_date(value, *, field_name: str):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        parsed = parse_date(value.strip())
        if parsed is not None:
            return parsed.isoformat()
    raise ValidationError({field_name: "Must be a valid date."})


def _normalize_datetime(value, *, field_name: str):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_datetime(value.strip())
        if dt is None:
            raise ValidationError({field_name: "Must be a valid datetime."})
    else:
        raise ValidationError({field_name: "Must be a valid datetime."})
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.isoformat()


def _normalize_string(value, *, field_name: str):
    if isinstance(value, (str, int, float)):
        candidate = str(value).strip()
        if not candidate:
            raise ValidationError({field_name: "Must not be blank."})
        return candidate
    if isinstance(value, UUID):
        return str(value)
    raise ValidationError({field_name: "Must be a string."})


def _normalize_scalar(value, schema: dict, *, field_name: str):
    if _is_blank(value):
        if _allowed_null(schema):
            return None
        raise ValidationError({field_name: "This field is required."})

    allowed_types = _normalize_required_types(schema)
    if "enum" in schema:
        choices = list(schema.get("enum") or [])
        if value not in choices:
            raise ValidationError({field_name: f"Unsupported value. Allowed values are: {', '.join(map(str, choices))}."})
        return deepcopy(value)
    if "choices" in schema:
        choices = list(schema.get("choices") or [])
        if value not in choices:
            raise ValidationError({field_name: f"Unsupported value. Allowed values are: {', '.join(map(str, choices))}."})
        return deepcopy(value)

    normalized_type = None
    for candidate_type in allowed_types:
        if candidate_type == "null":
            continue
        try:
            if candidate_type in {"str", "string"}:
                normalized_type = _normalize_string(value, field_name=field_name)
            elif candidate_type in {"int", "integer"}:
                normalized_type = _normalize_int(value, field_name=field_name)
            elif candidate_type in {"float", "number"}:
                normalized_type = _normalize_float(value, field_name=field_name)
            elif candidate_type in {"bool", "boolean"}:
                normalized_type = _normalize_bool(value, field_name=field_name)
            elif candidate_type == "date":
                normalized_type = _normalize_date(value, field_name=field_name)
            elif candidate_type == "datetime":
                normalized_type = _normalize_datetime(value, field_name=field_name)
            elif candidate_type in {"uuid", "guid"}:
                normalized_type = _normalize_uuid(value, field_name=field_name)
            else:
                normalized_type = deepcopy(value)
            break
        except ValidationError:
            normalized_type = None
    if normalized_type is None:
        raise ValidationError({field_name: "Unsupported value."})

    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and isinstance(normalized_type, (int, float)) and normalized_type < minimum:
        raise ValidationError({field_name: f"Must be greater than or equal to {minimum}."})
    if maximum is not None and isinstance(normalized_type, (int, float)) and normalized_type > maximum:
        raise ValidationError({field_name: f"Must be less than or equal to {maximum}."})

    return normalized_type


def _normalize_array(value, schema: dict, *, field_name: str):
    if _is_blank(value):
        if _allowed_null(schema):
            return None
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError({field_name: "Must be a list."})

    normalized = []
    item_schema = schema.get("items")
    for index, item in enumerate(value):
        if item_schema:
            item_field_name = f"{field_name}[{index}]"
            normalized.append(_normalize_schema_value(item, item_schema, field_name=item_field_name))
        else:
            normalized.append(deepcopy(item))

    min_items = schema.get("min_items", schema.get("minItems"))
    max_items = schema.get("max_items", schema.get("maxItems"))
    if min_items is not None and len(normalized) < int(min_items):
        raise ValidationError({field_name: f"Must contain at least {int(min_items)} item(s)."})
    if max_items is not None and len(normalized) > int(max_items):
        raise ValidationError({field_name: f"Must contain at most {int(max_items)} item(s)."})
    return normalized


def _normalize_object(value, schema: dict, *, field_name: str):
    if _is_blank(value):
        if _allowed_null(schema):
            return None
        raise ValidationError({field_name: "This field is required."})
    if not isinstance(value, dict):
        raise ValidationError({field_name: "Must be a dictionary/object."})

    properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    required = [str(entry) for entry in schema.get("required", []) if str(entry)]
    additional_properties = schema.get("additionalProperties", False)

    normalized: dict = {}
    errors: dict[str, list[str]] = {}

    for required_key in required:
        if required_key not in value or _is_blank(value.get(required_key)):
            _collect_error(errors, f"{field_name}.{required_key}" if field_name else required_key, "This field is required.")

    for key, raw_value in value.items():
        child_field_name = f"{field_name}.{key}" if field_name else key
        if key in properties:
            try:
                normalized[key] = _normalize_schema_value(raw_value, properties[key], field_name=child_field_name)
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for child_key, messages in exc.message_dict.items():
                        for message in messages:
                            _collect_error(errors, child_key, str(message))
                else:
                    for message in exc.messages:
                        _collect_error(errors, child_field_name, str(message))
        elif additional_properties is True:
            normalized[key] = deepcopy(raw_value)
        elif isinstance(additional_properties, dict):
            try:
                normalized[key] = _normalize_schema_value(raw_value, additional_properties, field_name=child_field_name)
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for child_key, messages in exc.message_dict.items():
                        for message in messages:
                            _collect_error(errors, child_key, str(message))
                else:
                    for message in exc.messages:
                        _collect_error(errors, child_field_name, str(message))
        else:
            _collect_error(errors, child_field_name, "Unsupported parameter.")

    if errors:
        raise ValidationError(errors)

    if schema.get("properties"):
        return normalized
    return deepcopy(value)


def _normalize_schema_value(value, schema: dict | None, *, field_name: str):
    schema = schema or {}
    if not isinstance(schema, dict):
        return deepcopy(value)

    schema_keys = {
        "type",
        "properties",
        "items",
        "enum",
        "choices",
        "required",
        "additionalProperties",
        "nullable",
        "minimum",
        "maximum",
        "min_items",
        "max_items",
        "minItems",
        "maxItems",
    }
    legacy_config_keys = {
        "default_columns",
        "optional_filters",
        "required_filters",
        "allowed_output_formats",
        "field_options",
        "aggregation_options",
        "default_parameters",
    }
    if isinstance(value, dict) and not any(key in schema for key in schema_keys) and any(
        key in schema for key in legacy_config_keys
    ):
        return deepcopy(value)

    candidates = _normalize_required_types(schema)
    if len(candidates) > 1:
        last_error = None
        for candidate in candidates:
            if candidate == "null" and _is_blank(value):
                return None
            candidate_schema = dict(schema)
            candidate_schema["type"] = candidate
            try:
                return _normalize_schema_value(value, candidate_schema, field_name=field_name)
            except ValidationError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error

    candidate_type = candidates[0] if candidates else "string"
    if candidate_type == "object":
        return _normalize_object(value, schema, field_name=field_name)
    if candidate_type == "array":
        return _normalize_array(value, schema, field_name=field_name)
    if candidate_type in {"null"}:
        return None
    return _normalize_scalar(value, schema, field_name=field_name)


def validate_parameter_schema(parameters: dict | None, schema: dict | None, *, field_name: str = "parameters") -> dict:
    parameters = parameters or {}
    if not isinstance(parameters, dict):
        raise ValidationError({field_name: "Must be a dictionary/object."})
    if schema and not isinstance(schema, dict):
        raise ValidationError({field_name: "Parameter schema must be a dictionary/object."})

    normalized = _normalize_schema_value(parameters, schema or {"type": "object", "properties": {}}, field_name=field_name)
    if not isinstance(normalized, dict):
        raise ValidationError({field_name: "Must be a dictionary/object."})

    start_value = normalized.get("date_from") or normalized.get("start_date")
    end_value = normalized.get("date_to") or normalized.get("end_date")
    if start_value and end_value:
        start_dt = parse_datetime(start_value) or parse_date(start_value)
        end_dt = parse_datetime(end_value) or parse_date(end_value)
        if start_dt and end_dt and start_dt > end_dt:
            raise ValidationError({field_name: "date_from/start_date must be earlier than date_to/end_date."})

    return normalized
