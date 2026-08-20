from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from datetime import timedelta
from functools import lru_cache

from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.devices.models import Device
from apps.telemetry.models import MetricDataType, MetricDefinition, TelemetryPoint
from apps.telemetry.services.history import floor_datetime, resolve_history_plan


NUMERIC_METRIC_TYPES = {MetricDataType.FLOAT, MetricDataType.INTEGER, MetricDataType.BOOLEAN}

MIRROR_AGGREGATES = {
    "telemetry_5m": "telemetry_points_ts_5m",
    "telemetry_1h": "telemetry_points_ts_1h",
    "telemetry_1d": "telemetry_points_ts_1d",
}


def _parse_date_range(parameters: dict | None):
    parameters = parameters or {}
    start_value = parameters.get("date_from") or parameters.get("start_date")
    end_value = parameters.get("date_to") or parameters.get("end_date")

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


def _device_queryset(*, organization, data_center=None, parameters: dict | None = None):
    parameters = parameters or {}
    qs = Device.objects.select_related("organization", "data_center", "room", "rack", "device_type", "device_model").filter(
        organization_id=organization.id
    )
    if data_center is not None:
        qs = qs.filter(data_center_id=data_center.id)
    if parameters.get("device_id"):
        qs = qs.filter(pk=parameters["device_id"])
    if parameters.get("room_id"):
        qs = qs.filter(room_id=parameters["room_id"])
    if parameters.get("rack_id"):
        qs = qs.filter(rack_id=parameters["rack_id"])
    if parameters.get("device_type_id"):
        qs = qs.filter(device_type_id=parameters["device_type_id"])
    if parameters.get("device_model_id"):
        qs = qs.filter(device_model_id=parameters["device_model_id"])
    return qs


@lru_cache(maxsize=1)
def _has_timescaledb():
    if connection.vendor != "postgresql":
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
            return cursor.fetchone() is not None
    except Exception:
        return False


def _relation_exists(name: str) -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", [name])
            return cursor.fetchone()[0] is not None
    except Exception:
        return False


def _raw_numeric_value(row: dict) -> float | None:
    value = row.get("value_float")
    if value is not None:
        return float(value)
    value = row.get("value_integer")
    if value is not None:
        return float(value)
    value = row.get("value_boolean")
    if value is not None:
        return 1.0 if value else 0.0
    value = row.get("avg_value")
    if value is not None:
        return float(value)
    return None


def _bucket_aggregate_rows(rows: list[dict], bucket_delta: timedelta) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        stamp = row.get("time")
        if not stamp:
            continue
        bucket_time = floor_datetime(stamp, bucket_delta)
        grouped[(row.get("device_id"), row.get("metric_id"), bucket_time)].append(row)

    bucketed: list[dict] = []
    for (device_id, metric_id, bucket_time), bucket_rows in sorted(
        grouped.items(), key=lambda item: (item[0][2], str(item[0][0]), str(item[0][1]))
    ):
        weighted_values: list[tuple[float, float]] = []
        min_candidates: list[float] = []
        max_candidates: list[float] = []
        sample_count = 0
        last_observed_at = None
        for row in bucket_rows:
            numeric_value = _raw_numeric_value(row)
            if numeric_value is not None:
                weight = float(row.get("sample_count") or 1)
                weighted_values.append((numeric_value, weight))
                min_value = row.get("min_value")
                max_value = row.get("max_value")
                if min_value is not None:
                    min_candidates.append(float(min_value))
                elif row.get("avg_value") is not None:
                    min_candidates.append(float(row["avg_value"]))
                if max_value is not None:
                    max_candidates.append(float(max_value))
                elif row.get("avg_value") is not None:
                    max_candidates.append(float(row["avg_value"]))
                sample_count += int(row.get("sample_count") or 1)
                observed_at = row.get("last_observed_at") or row.get("time")
                if observed_at is not None and (last_observed_at is None or observed_at > last_observed_at):
                    last_observed_at = observed_at
        total_weight = sum(weight for _, weight in weighted_values)
        average = (sum(value * weight for value, weight in weighted_values) / total_weight) if total_weight else None
        bucketed.append(
            {
                "time": bucket_time,
                "organization_id": bucket_rows[0].get("organization_id"),
                "data_center_id": bucket_rows[0].get("data_center_id"),
                "device_id": device_id,
                "metric_id": metric_id,
                "avg_value": average,
                "min_value": min(min_candidates) if min_candidates else None,
                "max_value": max(max_candidates) if max_candidates else None,
                "last_observed_at": last_observed_at or bucket_time,
                "sample_count": sample_count or len(bucket_rows),
                "quality": "BUCKETED",
                "source": bucket_rows[0].get("source") or "bucketed",
            }
        )
    return bucketed


def _fetch_raw_rows(*, organization, data_center, device_ids, metric_ids, start_dt, end_dt):
    qs = TelemetryPoint.objects.select_related(
        "organization",
        "data_center",
        "device",
        "device__room",
        "device__rack",
        "device__device_model",
        "device__device_type",
        "metric",
    ).filter(
        organization_id=organization.id,
        device_id__in=device_ids,
        metric_id__in=metric_ids,
    )
    if data_center is not None:
        qs = qs.filter(data_center_id=data_center.id)
    if start_dt is not None:
        qs = qs.filter(time__gte=start_dt)
    if end_dt is not None:
        qs = qs.filter(time__lte=end_dt)

    rows = list(
        qs.order_by("time", "device__name", "metric__code").values(
            "time",
            "organization_id",
            "data_center_id",
            "device_id",
            "device__name",
            "device__code",
            "device__room__name",
            "device__room__code",
            "device__rack__name",
            "device__rack__code",
            "device__device_model__name",
            "device__device_type__name",
            "metric_id",
            "metric__code",
            "metric__name",
            "metric__unit",
            "quality",
            "source",
            "value_float",
            "value_integer",
            "value_boolean",
            "value_text",
            "raw_value_text",
        )
    )
    for row in rows:
        row["device_name"] = row.pop("device__name")
        row["device_code"] = row.pop("device__code")
        row["room_name"] = row.pop("device__room__name")
        row["room_code"] = row.pop("device__room__code")
        row["rack_name"] = row.pop("device__rack__name")
        row["rack_code"] = row.pop("device__rack__code")
        row["device_model_name"] = row.pop("device__device_model__name")
        row["device_type_name"] = row.pop("device__device_type__name")
        row["metric_code"] = row.pop("metric__code")
        row["metric_name"] = row.pop("metric__name")
        row["unit"] = row.pop("metric__unit")
    return rows


def _fetch_aggregate_rows(*, table_name: str, organization_id, data_center_id, device_ids, metric_ids, start_dt, end_dt):
    query = f"""
        SELECT
            bucket AS time,
            organization_id,
            data_center_id,
            device_id,
            metric_id,
            avg_value,
            min_value,
            max_value,
            last_observed_at,
            sample_count
        FROM {table_name}
        WHERE organization_id = %s
          AND device_id = ANY(%s)
          AND metric_id = ANY(%s)
    """
    params = [organization_id, list(device_ids), list(metric_ids)]
    if data_center_id is not None:
        query += " AND data_center_id = %s"
        params.append(data_center_id)
    if start_dt is not None:
        query += " AND bucket >= %s"
        params.append(start_dt)
    if end_dt is not None:
        query += " AND bucket <= %s"
        params.append(end_dt)
    query += " ORDER BY bucket, device_id, metric_id"

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in rows:
        row["source"] = table_name
        row["quality"] = row.get("quality") or "BUCKETED"
    return rows


def _decorate_rows(*, rows: list[dict], devices_by_id: dict[str, Device], metrics_by_id: dict[str, MetricDefinition], aggregate: bool):
    payload: list[dict] = []
    for row in rows:
        device = devices_by_id.get(str(row.get("device_id")))
        metric = metrics_by_id.get(str(row.get("metric_id")))
        if device is None or metric is None:
            continue
        if aggregate:
            value = row.get("avg_value")
            if value is not None:
                if metric.data_type == MetricDataType.INTEGER:
                    value = int(round(float(value)))
                elif metric.data_type == MetricDataType.BOOLEAN:
                    value = bool(round(float(value)))
                else:
                    value = float(value)
        else:
            value = row.get("value_float")
            if value is None:
                value = row.get("value_integer")
            if value is None:
                value = row.get("value_boolean")
            if value is None:
                value = row.get("value_text") or row.get("raw_value_text")

        payload.append(
            {
                "timestamp": row.get("time"),
                "organization": getattr(device.organization, "name", None),
                "data_center": getattr(device.data_center, "name", None),
                "room": getattr(getattr(device, "room", None), "name", None),
                "room_name": getattr(getattr(device, "room", None), "name", None),
                "room_code": getattr(getattr(device, "room", None), "code", None),
                "rack": getattr(getattr(device, "rack", None), "name", None),
                "rack_name": getattr(getattr(device, "rack", None), "name", None),
                "rack_code": getattr(getattr(device, "rack", None), "code", None),
                "device": device.name,
                "device_name": device.name,
                "device_code": device.code,
                "device_model": getattr(getattr(device, "device_model", None), "name", None),
                "device_model_name": getattr(getattr(device, "device_model", None), "name", None),
                "device_type": getattr(getattr(device, "device_type", None), "name", None),
                "device_type_name": getattr(getattr(device, "device_type", None), "name", None),
                "metric_code": metric.code,
                "metric_name": metric.name,
                "value": value,
                "unit": metric.unit,
                "quality": row.get("quality") or ("BUCKETED" if aggregate else None),
                "source": row.get("source"),
            }
        )
    return payload


def fetch_telemetry_report_rows(*, organization, data_center=None, metric_codes: list[str] | None = None, parameters: dict | None = None):
    parameters = parameters or {}
    metric_codes = [str(code).strip() for code in (metric_codes or []) if str(code).strip()]
    if not metric_codes:
        return [], []

    metrics = list(MetricDefinition.objects.filter(code__in=metric_codes, is_active=True).order_by("name", "code"))
    if not metrics:
        return [], []

    metric_ids = [metric.id for metric in metrics]
    devices = list(_device_queryset(organization=organization, data_center=data_center, parameters=parameters))
    if not devices:
        return [], metrics

    devices_by_id = {str(device.id): device for device in devices}
    metrics_by_id = {str(metric.id): metric for metric in metrics}

    start_dt, end_dt = _parse_date_range(parameters)
    if start_dt and end_dt:
        plan = resolve_history_plan(start_dt, end_dt)
    else:
        plan = None

    aggregate_source = None
    bucket_delta = None
    if plan is not None:
        bucket_delta = plan.target_bucket or plan.source_bucket or timedelta(minutes=5)

    aggregate_source = None
    if plan is not None:
        aggregate_source = MIRROR_AGGREGATES.get(plan.source, MIRROR_AGGREGATES["telemetry_5m"])
        use_aggregate = bool(
            _has_timescaledb()
            and aggregate_source
            and _relation_exists(aggregate_source)
            and all(metric.data_type in NUMERIC_METRIC_TYPES for metric in metrics)
        )
        if use_aggregate:
            start_bucket = floor_datetime(start_dt, bucket_delta)
            end_bucket = floor_datetime(end_dt, bucket_delta)
            try:
                rows = _fetch_aggregate_rows(
                    table_name=aggregate_source,
                    organization_id=organization.id,
                    data_center_id=getattr(data_center, "id", None),
                    device_ids=[device.id for device in devices],
                    metric_ids=metric_ids,
                    start_dt=start_bucket,
                    end_dt=end_bucket,
                )
                if rows:
                    if plan.source_bucket != plan.target_bucket:
                        rows = _bucket_aggregate_rows(rows, bucket_delta)
                    payload = _decorate_rows(rows=rows, devices_by_id=devices_by_id, metrics_by_id=metrics_by_id, aggregate=True)
                    if payload:
                        return payload, metrics
            except Exception:
                rows = []

    rows = _fetch_raw_rows(
        organization=organization,
        data_center=data_center,
        device_ids=[device.id for device in devices],
        metric_ids=metric_ids,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    return _decorate_rows(rows=rows, devices_by_id=devices_by_id, metrics_by_id=metrics_by_id, aggregate=False), metrics
