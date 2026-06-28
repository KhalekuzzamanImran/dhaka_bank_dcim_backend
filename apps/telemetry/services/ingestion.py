from django.utils import timezone
from apps.telemetry.models import TelemetryPoint, LatestTelemetry


def store_telemetry_point(*, device, metric, value, source=None, quality="GOOD", ingest_id=None, ts=None):
    ts = ts or timezone.now()
    value_kwargs = {"value_float": None, "value_integer": None, "value_boolean": None, "value_text": None}
    if isinstance(value, bool):
        value_kwargs["value_boolean"] = value
    elif isinstance(value, int):
        value_kwargs["value_integer"] = value
    elif isinstance(value, float):
        value_kwargs["value_float"] = value
    else:
        value_kwargs["value_text"] = str(value)

    point = TelemetryPoint.objects.create(
        time=ts,
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        metric=metric,
        quality=quality,
        source=source,
        ingest_id=ingest_id,
        **value_kwargs,
    )
    LatestTelemetry.objects.update_or_create(
        device=device,
        metric=metric,
        defaults={
            "organization": device.organization,
            "data_center": device.data_center,
            "last_seen_at": ts,
            "quality": quality,
            "source": source,
            **value_kwargs,
        },
    )
    return point
