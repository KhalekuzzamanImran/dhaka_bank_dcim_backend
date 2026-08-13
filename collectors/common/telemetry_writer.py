from django.db import transaction
from django.utils import timezone

from apps.live_updates.services import publish_live_update, telemetry_delta_from_latest
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


def _refresh_scopes(device):
    device_type_code = str(getattr(getattr(device, "device_type", None), "code", "") or "").strip().upper()
    scopes = [
        "overview",
        f"device:{device.pk}",
        f"organization:{device.organization_id}",
        f"data_center:{device.data_center_id}",
    ]
    if device_type_code:
        scopes.append(f"device_type:{device_type_code}")
    return scopes


@transaction.atomic
def write_device_telemetry_bulk(*, organization, data_center, device, readings, source, ingest_id=None, timestamp=None):
    timestamp = timestamp or timezone.now()
    points = []
    latest_rows = []
    telemetry_deltas = []
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
        latest, _ = LatestTelemetry.objects.update_or_create(
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
        telemetry_deltas.append(telemetry_delta_from_latest(latest, observed_at=timestamp))
    if latest_rows:
        transaction.on_commit(
            lambda: publish_live_update(
                event_type="telemetry_batch",
                resource_type="Device",
                resource_id=device.pk,
                scopes=_refresh_scopes(device),
                metadata={
                    "device_id": str(device.pk),
                    "device_type": str(getattr(getattr(device, "device_type", None), "code", "") or ""),
                    "source": source,
                    "ingest_id": str(ingest_id) if ingest_id else None,
                    "point_count": len(points),
                    "metrics": telemetry_deltas,
                },
                delivery_scopes=[
                    "global",
                    f"organization:{organization.pk}",
                    f"data_center:{data_center.pk}",
                    f"device:{device.pk}",
                ],
            )
        )
    return len(points)
