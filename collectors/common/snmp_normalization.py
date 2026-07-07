import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, MutableMapping

from apps.telemetry.models import MetricDataType

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on", "online", "ok", "active"}
_FALSE_VALUES = {"0", "false", "no", "off", "offline", "inactive"}
_NUMERIC_MAPPING_TYPES = {"FLOAT", "FLOAT32", "FLOAT64", "INTEGER", "INT", "COUNTER", "GAUGE", "UINT16", "UINT32", "INT16", "INT32", "UINT64", "INT64"}
_TEXT_MAPPING_TYPES = {"TEXT", "STRING", "STR"}
_BOOL_MAPPING_TYPES = {"BOOLEAN", "BOOL"}


def _normalize_type_label(value: Any) -> str:
    return str(value or "").strip().upper()


def _raw_text(raw_value: Any) -> str:
    if raw_value is None:
        return ""
    if hasattr(raw_value, "prettyPrint"):
        return str(raw_value.prettyPrint())
    return str(raw_value)


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    return Decimal(str(value))


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return bool(value)
    text = _raw_text(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return bool(text)


def parse_snmp_raw_value(raw_value: Any, mapping_data_type: Any) -> Any:
    """Parse the raw SNMP response using the mapping data type only."""
    data_type = _normalize_type_label(mapping_data_type)
    raw_text = _raw_text(raw_value)

    if not data_type or data_type in _TEXT_MAPPING_TYPES:
        return raw_text

    if data_type in _BOOL_MAPPING_TYPES:
        return _coerce_bool(raw_text)

    if data_type in _NUMERIC_MAPPING_TYPES:
        try:
            numeric = _to_decimal(raw_text)
        except (InvalidOperation, TypeError, ValueError):
            logger.warning("Failed to parse SNMP raw numeric value raw_value=%r mapping_data_type=%s", raw_text, data_type)
            return raw_text
        if data_type in {"INTEGER", "INT", "COUNTER", "GAUGE", "UINT16", "UINT32", "INT16", "INT32", "UINT64", "INT64"}:
            if numeric == numeric.to_integral_value():
                return int(numeric)
            logger.warning("SNMP integer mapping received fractional value raw_value=%r mapping_data_type=%s", raw_text, data_type)
            return float(numeric)
        return float(numeric)

    return raw_text


def apply_scale_offset(parsed_value: Any, scale_factor: Any, offset_value: Any) -> Any:
    """Apply scale and offset after parsing the raw SNMP value."""
    if parsed_value is None:
        return None

    if isinstance(parsed_value, bool):
        scale = _to_decimal(scale_factor)
        offset = _to_decimal(offset_value)
        if scale == 1 and offset == 0:
            return parsed_value
        base = Decimal(1 if parsed_value else 0)
        value = (base * scale) + offset
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    if isinstance(parsed_value, (int, float, Decimal)):
        try:
            value = (_to_decimal(parsed_value) * _to_decimal(scale_factor)) + _to_decimal(offset_value)
        except (InvalidOperation, TypeError, ValueError):
            logger.warning(
                "Failed to apply SNMP scale/offset parsed_value=%r scale_factor=%r offset_value=%r",
                parsed_value,
                scale_factor,
                offset_value,
            )
            return parsed_value
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    return parsed_value


def store_value_by_metric_type(target: MutableMapping[str, Any] | Any, metric_data_type: Any, final_value: Any):
    """Store the final normalized value in the DB column that matches the metric type."""
    metric_type = _normalize_type_label(metric_data_type)
    value_fields = {
        "value_float": None,
        "value_integer": None,
        "value_boolean": None,
        "value_text": None,
    }

    if final_value is None:
        pass
    elif metric_type == MetricDataType.FLOAT:
        try:
            value_fields["value_float"] = float(final_value)
        except (TypeError, ValueError):
            logger.warning("Failed to coerce final SNMP value to float final_value=%r metric_data_type=%s", final_value, metric_type)
            value_fields["value_text"] = str(final_value)
    elif metric_type == MetricDataType.INTEGER:
        try:
            numeric = _to_decimal(final_value)
            if numeric != numeric.to_integral_value():
                logger.warning(
                    "Fractional value stored for integer metric final_value=%r metric_data_type=%s; rounding half up",
                    final_value,
                    metric_type,
                )
            value_fields["value_integer"] = int(numeric.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning("Failed to coerce final SNMP value to integer final_value=%r metric_data_type=%s", final_value, metric_type)
            value_fields["value_text"] = str(final_value)
    elif metric_type == MetricDataType.BOOLEAN:
        value_fields["value_boolean"] = _coerce_bool(final_value)
    else:
        value_fields["value_text"] = str(final_value)

    if target is None:
        return value_fields

    if hasattr(target, "update"):
        target.update(value_fields)
        return target

    for field_name, field_value in value_fields.items():
        setattr(target, field_name, field_value)
    return target

