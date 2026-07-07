from django.db import transaction
from django.utils import timezone

from apps.telemetry.models import TelemetryPoint, LatestTelemetry, TelemetryQuality
from .snmp_normalization import store_value_by_metric_type


def _payload_for(metric_data_type, value):
    return store_value_by_metric_type(None, metric_data_type, value)


def _quality_for_metric(metric_data_type, payload, fallback_quality):
    metric_type = str(metric_data_type or "").strip().upper()
    if metric_type == "FLOAT" and payload.get("value_float") is None:
        return TelemetryQuality.BAD
    if metric_type == "INTEGER" and payload.get("value_integer") is None:
        return TelemetryQuality.BAD
    if metric_type == "BOOLEAN" and payload.get("value_boolean") is None:
        return TelemetryQuality.BAD
    if metric_type in {"TEXT", "STRING", "STR"} and payload.get("value_text") in (None, ""):
        return TelemetryQuality.BAD
    return fallback_quality


@transaction.atomic
def write_device_telemetry_bulk(*, organization, data_center, device, readings, source, ingest_id=None, timestamp=None):
    timestamp = timestamp or timezone.now()
    points = []
    latest_rows = []
    for reading in readings:
        metric = reading["metric"]
        value = reading["value"]
        quality = reading.get("quality", TelemetryQuality.GOOD)
        payload = _payload_for(metric.data_type, value)
        quality = _quality_for_metric(metric.data_type, payload, payload.pop("quality", quality))
        common = {
            "organization": organization,
            "data_center": data_center,
            "device": device,
            "metric": metric,
            "quality": quality,
            "source": source,
        }
        raw_value_text = reading.get("raw_value_text")
        points.append(TelemetryPoint(time=timestamp, ingest_id=ingest_id, raw_value_text=raw_value_text, **common, **payload))
        latest_rows.append((metric, quality, payload, raw_value_text))
    if points:
        TelemetryPoint.objects.bulk_create(points, batch_size=1000)
    # Simple safe upsert for first production. Replace with ON CONFLICT for very high write volume.
    for metric, quality, payload, raw_value_text in latest_rows:
        LatestTelemetry.objects.update_or_create(
            device=device,
            metric=metric,
            defaults={
                "organization": organization,
                "data_center": data_center,
                "quality": quality,
                "last_seen_at": timestamp,
                "source": source,
                "raw_value_text": raw_value_text,
                "value_float": payload.get("value_float"),
                "value_integer": payload.get("value_integer"),
                "value_boolean": payload.get("value_boolean"),
                "value_text": payload.get("value_text"),
            },
        )
    return len(points)
