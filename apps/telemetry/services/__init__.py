"""Telemetry service facade.

This package-level module intentionally exports the public telemetry service
functions used by API views and collectors. The project contains a
``services/`` package, so imports like ``from apps.telemetry.services import
 ingest_points`` resolve here, not to the sibling ``services.py`` file.
"""
import uuid

from django.db import transaction
from django.utils import timezone

from apps.devices.models import Device, DeviceStatus
from apps.telemetry.models import (
    LatestTelemetry,
    MetricDefinition,
    TelemetryIngestLog,
    TelemetryPoint,
)
from .ingestion import store_telemetry_point


@transaction.atomic
def ingest_points(points, source="api"):
    """Ingest normalized telemetry payloads from the REST API.

    Expected point shape:
        {
            "device": "<device uuid>",
            "metric_code": "ups_load_percent",
            "time": optional datetime,
            "value_float": optional,
            "value_integer": optional,
            "value_boolean": optional,
            "value_text": optional,
            "quality": optional,
            "source": optional,
        }
    """
    ingest_id = uuid.uuid4()
    now = timezone.now()
    created = []

    for item in points:
        device = Device.objects.select_related("organization", "data_center").get(id=item["device"])
        metric = MetricDefinition.objects.get(code=item["metric_code"])
        ts = item.get("time") or now
        point_source = item.get("source") or source
        quality = item.get("quality", "GOOD")

        common = {
            "organization": device.organization,
            "data_center": device.data_center,
            "device": device,
            "metric": metric,
            "value_float": item.get("value_float"),
            "value_integer": item.get("value_integer"),
            "value_boolean": item.get("value_boolean"),
            "value_text": item.get("value_text"),
            "raw_value_text": item.get("raw_value_text"),
            "quality": quality,
            "source": point_source,
        }

        point = TelemetryPoint.objects.create(time=ts, ingest_id=ingest_id, **common)
        LatestTelemetry.objects.update_or_create(
            device=device,
            metric=metric,
            defaults={**common, "last_seen_at": ts},
        )
        Device.objects.filter(pk=device.pk).update(last_seen_at=ts, status=DeviceStatus.ONLINE)
        created.append(point)

    first_device = created[0].device if created else None
    finished_at = timezone.now()
    TelemetryIngestLog.objects.create(
        ingest_id=ingest_id,
        device=first_device,
        protocol=source,
        status="SUCCESS",
        raw_payload={"point_count": len(created)},
        started_at=now,
        finished_at=finished_at,
        duration_ms=int((finished_at - now).total_seconds() * 1000),
    )
    return ingest_id, created


__all__ = ["ingest_points", "store_telemetry_point"]
