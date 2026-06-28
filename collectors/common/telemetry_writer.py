from django.db import transaction
from django.utils import timezone

from apps.telemetry.models import TelemetryPoint, LatestTelemetry, MetricDataType, TelemetryQuality


def _payload_for(metric_data_type, value):
    metric_data_type = str(metric_data_type).upper()
    if metric_data_type == MetricDataType.FLOAT:
        try:
            return {"value_float": float(value)}
        except Exception:
            return {"value_text": str(value), "quality": TelemetryQuality.BAD}
    if metric_data_type == MetricDataType.INTEGER:
        try:
            return {"value_integer": int(value)}
        except Exception:
            return {"value_text": str(value), "quality": TelemetryQuality.BAD}
    if metric_data_type == MetricDataType.BOOLEAN:
        return {"value_boolean": bool(value)}
    return {"value_text": str(value)}


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
        quality = payload.pop("quality", quality)
        common = {
            "organization": organization,
            "data_center": data_center,
            "device": device,
            "metric": metric,
            "quality": quality,
            "source": source,
        }
        points.append(TelemetryPoint(time=timestamp, ingest_id=ingest_id, **common, **payload))
        latest_rows.append((metric, quality, payload))
    if points:
        TelemetryPoint.objects.bulk_create(points, batch_size=1000)
    # Simple safe upsert for first production. Replace with ON CONFLICT for very high write volume.
    for metric, quality, payload in latest_rows:
        LatestTelemetry.objects.update_or_create(
            device=device,
            metric=metric,
            defaults={
                "organization": organization,
                "data_center": data_center,
                "quality": quality,
                "last_seen_at": timestamp,
                "source": source,
                "value_float": payload.get("value_float"),
                "value_integer": payload.get("value_integer"),
                "value_boolean": payload.get("value_boolean"),
                "value_text": payload.get("value_text"),
            },
        )
    return len(points)
