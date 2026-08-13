from django.db import transaction
from decimal import Decimal

from django.utils import timezone
from apps.telemetry.models import TelemetryPoint, LatestTelemetry
from apps.live_updates.services import publish_live_update, telemetry_delta_from_latest


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
def store_telemetry_point(*, device, metric, value, source=None, quality="GOOD", ingest_id=None, ts=None):
    ts = ts or timezone.now()
    value_kwargs = {"value_float": None, "value_integer": None, "value_boolean": None, "value_text": None}
    raw_value_text = None
    if isinstance(value, bool):
        value_kwargs["value_boolean"] = value
    elif isinstance(value, int):
        value_kwargs["value_integer"] = value
    elif isinstance(value, float):
        value_kwargs["value_float"] = value
    elif isinstance(value, Decimal):
        if value == value.to_integral_value():
            value_kwargs["value_integer"] = int(value)
        else:
            value_kwargs["value_float"] = float(value)
    else:
        value_kwargs["value_text"] = str(value)
    raw_value_text = str(value)

    point = TelemetryPoint.objects.create(
        time=ts,
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        metric=metric,
        quality=quality,
        source=source,
        ingest_id=ingest_id,
        raw_value_text=raw_value_text,
        **value_kwargs,
    )
    latest, _ = LatestTelemetry.objects.update_or_create(
        device=device,
        metric=metric,
        defaults={
            "organization": device.organization,
            "data_center": device.data_center,
            "last_seen_at": ts,
            "quality": quality,
            "source": source,
            "raw_value_text": raw_value_text,
            **value_kwargs,
        },
    )
    transaction.on_commit(
        lambda: publish_live_update(
            event_type="telemetry_batch",
            resource_type="Device",
            resource_id=device.pk,
            scopes=_refresh_scopes(device),
            metadata={
                "device_id": str(device.pk),
                "metric_code": str(getattr(metric, "code", "") or ""),
                "source": source,
                "ingest_id": str(ingest_id) if ingest_id else None,
                "metrics": [telemetry_delta_from_latest(latest, observed_at=ts)],
            },
            delivery_scopes=[
                "global",
                f"organization:{device.organization_id}",
                f"data_center:{device.data_center_id}",
                f"device:{device.pk}",
            ],
        )
    )
    return point
