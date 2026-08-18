"""Telemetry service facade.

This package-level module intentionally exports the public telemetry service
functions used by API views and collectors. The project contains a
``services/`` package, so imports like ``from apps.telemetry.services import
 ingest_points`` resolve here, not to the sibling ``services.py`` file.
"""
import uuid

from django.db import transaction
from django.utils import timezone

from apps.live_updates.services import publish_live_update, telemetry_delta_from_latest
from apps.live_updates.services import publish_device_status_update
from apps.devices.models import Device, DeviceStatus
from apps.telemetry.models import (
    LatestTelemetry,
    MetricDefinition,
    TelemetryIngestLog,
    TelemetryPoint,
)
from .ingestion import store_telemetry_point


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
    telemetry_deltas = []
    device_status_updates = {}

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
        latest, _ = LatestTelemetry.objects.update_or_create(
            device=device,
            metric=metric,
            defaults={**common, "last_seen_at": ts},
        )
        telemetry_deltas.append(telemetry_delta_from_latest(latest, observed_at=ts))
        previous_status = device.status
        updated = Device.objects.filter(pk=device.pk).update(last_seen_at=ts, status=DeviceStatus.ONLINE)
        if updated and str(previous_status or "").upper() != DeviceStatus.ONLINE:
            device_status_updates[str(device.pk)] = {
                "device": device,
                "previous_status": previous_status,
                "observed_at": ts,
            }
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
    if first_device:
        transaction.on_commit(
            lambda: publish_live_update(
                event_type="telemetry_batch",
                resource_type="Device",
                resource_id=first_device.pk,
                scopes=_refresh_scopes(first_device),
                metadata={
                    "device_id": str(first_device.pk),
                    "device_type": str(getattr(getattr(first_device, "device_type", None), "code", "") or ""),
                    "source": source,
                    "ingest_id": str(ingest_id),
                    "point_count": len(created),
                    "metrics": telemetry_deltas,
                },
                delivery_scopes=[
                    "global",
                    f"organization:{first_device.organization_id}",
                    f"data_center:{first_device.data_center_id}",
                    f"device:{first_device.pk}",
                ],
            )
        )
    for update in device_status_updates.values():
        transaction.on_commit(
            lambda update=update: publish_device_status_update(
                update["device"],
                status=DeviceStatus.ONLINE,
                previous_status=update["previous_status"],
                reason="telemetry ingest",
                source=source,
                observed_at=update["observed_at"],
            )
        )
    return ingest_id, created


__all__ = ["ingest_points", "store_telemetry_point"]
