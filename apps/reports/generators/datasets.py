from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def normalize_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    result: list[str] = []
    seen = set()
    for raw in values:
        candidate = str(raw).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def format_datetime(value: Any, tz=None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if not hasattr(value, "isoformat"):
        return str(value)
    dt = value
    try:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, tz or timezone.get_current_timezone())
        elif tz is not None:
            dt = dt.astimezone(tz)
        return timezone.localtime(dt, tz).strftime("%Y-%m-%d %H:%M:%S") if tz is not None else timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return dt.isoformat(timespec="seconds")
        except Exception:
            return str(dt)


def parse_date_range(parameters: dict[str, Any], *, required: bool = False):
    start_value = parameters.get("date_from") or parameters.get("start_date")
    end_value = parameters.get("date_to") or parameters.get("end_date")
    if start_value in (None, "") and end_value in (None, ""):
        if required:
            raise ValueError("date_from and date_to are required.")
        return None, None
    if required and (start_value in (None, "") or end_value in (None, "")):
        raise ValueError("date_from and date_to are required.")

    def _parse(value, *, is_end: bool):
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, date):
            from datetime import time as dt_time, datetime as dt_cls

            dt = dt_cls.combine(value, dt_time.max if is_end else dt_time.min)
        else:
            parsed_dt = parse_datetime(str(value))
            if parsed_dt is not None:
                dt = parsed_dt
            else:
                parsed_date = parse_date(str(value))
                if parsed_date is None:
                    raise ValueError(f"Invalid date value: {value!r}")
                from datetime import time as dt_time, datetime as dt_cls

                dt = dt_cls.combine(parsed_date, dt_time.max if is_end else dt_time.min)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    start_dt = _parse(start_value, is_end=False) if start_value not in (None, "") else None
    end_dt = _parse(end_value, is_end=True) if end_value not in (None, "") else None
    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("Invalid date range: date_from/start_date must be earlier than date_to/end_date.")
    return start_dt, end_dt


def normalize_telemetry_metrics(value) -> list[str]:
    return normalize_list(value)


TELEMETRY_METRIC_CODE_ALIASES = {
    "pac_temperature": "pac_room_temperature",
    "pac_humidity": "pac_room_humidity",
    "room_temperature": "pac_room_temperature",
    "room_humidity": "pac_room_humidity",
    "roomTemp": "pac_room_temperature",
    "roomRH": "pac_room_humidity",
}


def normalize_telemetry_metric_codes(value) -> list[str]:
    codes = normalize_list(value)
    if not codes:
        return []
    normalized: list[str] = []
    seen = set()
    for code in codes:
        canonical = TELEMETRY_METRIC_CODE_ALIASES.get(code, code)
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)
    return normalized


def format_cell_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime, date)):
        return format_datetime(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return int(value)
        return float(value)
    return value


def deep_copy_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): deep_copy_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [deep_copy_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [deep_copy_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return format_datetime(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def flatten_table_rows(columns: list[str], rows: Iterable[dict[str, Any]]) -> list[list[Any]]:
    return [[format_cell_value(row.get(column)) for column in columns] for row in rows]
