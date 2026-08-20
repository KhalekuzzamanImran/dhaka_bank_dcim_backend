from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

from django.db import connection

from apps.telemetry.models import TelemetryPoint


RAW_WINDOW = timedelta(hours=6)
WINDOW_24H = timedelta(days=1)
WINDOW_7D = timedelta(days=7)
WINDOW_30D = timedelta(days=30)
WINDOW_90D = timedelta(days=90)
WINDOW_6M = timedelta(days=183)

AGG_5M = "telemetry_5m"
AGG_1H = "telemetry_1h"
AGG_1D = "telemetry_1d"


@dataclass(frozen=True)
class HistoryPlan:
    source: str
    source_bucket: timedelta | None
    target_bucket: timedelta | None


def _bucket_seconds(bucket_delta: timedelta) -> int:
    return int(bucket_delta.total_seconds())


def floor_datetime(value, bucket_delta: timedelta):
    if bucket_delta is None:
        return value
    timestamp = value.timestamp()
    bucket_size = _bucket_seconds(bucket_delta)
    floored = timestamp - (timestamp % bucket_size)
    return value.__class__.fromtimestamp(floored, tz=value.tzinfo)


def iter_bucket_sequence(start_dt, end_dt, bucket_delta: timedelta):
    current = floor_datetime(start_dt, bucket_delta)
    end_bucket = floor_datetime(end_dt, bucket_delta)
    while current <= end_bucket:
        yield current
        current += bucket_delta


def resolve_history_plan(start_dt, end_dt) -> HistoryPlan:
    duration = end_dt - start_dt
    if duration <= RAW_WINDOW:
        return HistoryPlan(source="raw", source_bucket=None, target_bucket=None)
    if duration <= WINDOW_24H:
        return HistoryPlan(source=AGG_5M, source_bucket=timedelta(minutes=5), target_bucket=timedelta(minutes=5))
    if duration <= WINDOW_7D:
        return HistoryPlan(source=AGG_5M, source_bucket=timedelta(minutes=5), target_bucket=timedelta(minutes=30))
    if duration <= WINDOW_30D:
        return HistoryPlan(source=AGG_1H, source_bucket=timedelta(hours=1), target_bucket=timedelta(hours=1))
    if duration <= WINDOW_90D:
        return HistoryPlan(source=AGG_1H, source_bucket=timedelta(hours=1), target_bucket=timedelta(hours=3))
    if duration <= WINDOW_6M:
        return HistoryPlan(source=AGG_1H, source_bucket=timedelta(hours=1), target_bucket=timedelta(hours=6))
    return HistoryPlan(source=AGG_1D, source_bucket=timedelta(days=1), target_bucket=timedelta(days=1))


def _values_to_float(row):
    value = row.get("value_float")
    if value is not None:
        return float(value)
    value = row.get("value_integer")
    if value is not None:
        return float(value)
    value = row.get("value_boolean")
    if value is not None:
        return 1.0 if value else 0.0
    return None


def _bucket_rows(rows, target_bucket: timedelta):
    grouped = defaultdict(list)
    for row in rows:
        stamp = row.get("time")
        if not stamp:
            continue
        grouped[floor_datetime(stamp, target_bucket)].append(row)

    bucketed = []
    for bucket_time, bucket_rows in sorted(grouped.items(), key=lambda item: item[0]):
        values = [value for value in (_values_to_float(row) for row in bucket_rows) if value is not None]
        bucketed.append(
            {
                "time": bucket_time,
                "metric_code": bucket_rows[0].get("metric_code"),
                "quality": "BUCKETED",
                "source": "bucketed",
                "value_float": (sum(values) / len(values)) if values else None,
                "value_integer": None,
                "value_boolean": None,
                "value_text": None,
            }
        )
    return bucketed


def _fill_missing_buckets(rows, start_dt, end_dt, bucket_delta: timedelta):
    row_map = {row["time"]: row for row in rows if row.get("time") is not None}
    metric_code = rows[0].get("metric_code") if rows else None
    payload = []
    for bucket_time in iter_bucket_sequence(start_dt, end_dt, bucket_delta):
        row = row_map.get(bucket_time)
        if row is None:
            payload.append(
                {
                    "time": bucket_time,
                    "metric_code": metric_code,
                    "quality": "BUCKETED",
                    "source": "bucketed",
                    "value_float": None,
                    "value_integer": None,
                    "value_boolean": None,
                    "value_text": None,
                }
            )
            continue
        payload.append(row)
    return payload


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


def _fetch_raw_rows(device_id, metric_id, start_dt, end_dt):
    rows = list(
        TelemetryPoint.objects.filter(
            device_id=device_id,
            metric_id=metric_id,
            time__gte=start_dt,
            time__lte=end_dt,
        )
        .order_by("time")
        .values(
            "time",
            "metric__code",
            "quality",
            "source",
            "value_float",
            "value_integer",
            "value_boolean",
            "value_text",
        )
    )
    for row in rows:
        row["metric_code"] = row.pop("metric__code")
    return rows


def _fetch_aggregate_rows(table_name: str, device_id, metric_id, start_dt, end_dt):
    query = f"""
        SELECT
            bucket AS time,
            avg_value AS value_float,
            NULL::bigint AS value_integer,
            NULL::boolean AS value_boolean,
            NULL::text AS value_text,
            NULL::text AS quality
        FROM {table_name}
        WHERE device_id = %s
          AND metric_id = %s
          AND bucket >= %s
          AND bucket <= %s
        ORDER BY bucket
    """
    with connection.cursor() as cursor:
        cursor.execute(query, [device_id, metric_id, start_dt, end_dt])
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return rows


def get_telemetry_history_rows(*, device_id, metric, start_dt, end_dt):
    plan = resolve_history_plan(start_dt, end_dt)
    bucket_delta = plan.target_bucket or plan.source_bucket

    if plan.source == "raw":
        return _fetch_raw_rows(device_id, metric.id, start_dt, end_dt)

    if connection.vendor != "postgresql" or not _has_timescaledb():
        raw_rows = _fetch_raw_rows(device_id, metric.id, start_dt, end_dt)
        bucketed = _bucket_rows(raw_rows, bucket_delta or timedelta(hours=1))
        return _fill_missing_buckets(bucketed, start_dt, end_dt, bucket_delta or timedelta(hours=1))

    if plan.source == AGG_5M and not _relation_exists(AGG_5M):
        raw_rows = _fetch_raw_rows(device_id, metric.id, start_dt, end_dt)
        bucketed = _bucket_rows(raw_rows, bucket_delta or timedelta(hours=1))
        return _fill_missing_buckets(bucketed, start_dt, end_dt, bucket_delta or timedelta(hours=1))
    if plan.source == AGG_1H and not _relation_exists(AGG_1H):
        raw_rows = _fetch_raw_rows(device_id, metric.id, start_dt, end_dt)
        bucketed = _bucket_rows(raw_rows, bucket_delta or timedelta(hours=1))
        return _fill_missing_buckets(bucketed, start_dt, end_dt, bucket_delta or timedelta(hours=1))
    if plan.source == AGG_1D and not _relation_exists(AGG_1D):
        raw_rows = _fetch_raw_rows(device_id, metric.id, start_dt, end_dt)
        bucketed = _bucket_rows(raw_rows, bucket_delta or timedelta(hours=1))
        return _fill_missing_buckets(bucketed, start_dt, end_dt, bucket_delta or timedelta(hours=1))

    start_bucket = floor_datetime(start_dt, bucket_delta) if bucket_delta else start_dt
    end_bucket = floor_datetime(end_dt, bucket_delta) if bucket_delta else end_dt
    rows = _fetch_aggregate_rows(plan.source, device_id, metric.id, start_bucket, end_bucket)

    if plan.source_bucket == plan.target_bucket:
        normalized = []
        for row in rows:
            normalized.append(
                {
                    **row,
                    "metric_code": metric.code,
                    "source": plan.source,
                    "quality": row.get("quality") or "BUCKETED",
                }
            )
        return _fill_missing_buckets(normalized, start_dt, end_dt, bucket_delta)

    rebucketed = _bucket_rows(
        [
            {
                "time": row["time"],
                "metric_code": metric.code,
                "value_float": row.get("value_float"),
                "value_integer": row.get("value_integer"),
                "value_boolean": row.get("value_boolean"),
                "value_text": row.get("value_text"),
            }
            for row in rows
        ],
        bucket_delta,
    )
    return _fill_missing_buckets(rebucketed, start_dt, end_dt, bucket_delta)
