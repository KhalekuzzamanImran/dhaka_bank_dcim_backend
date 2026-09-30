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
from .history import get_telemetry_history_rows, resolve_history_plan


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
    points = list(points)
    ingest_id = uuid.uuid4()
    now = timezone.now()
    if not points:
        finished_at = timezone.now()
        TelemetryIngestLog.objects.create(
            ingest_id=ingest_id,
            protocol=source,
            status="SUCCESS",
            raw_payload={"point_count": 0},
            started_at=now,
            finished_at=finished_at,
            duration_ms=int((finished_at - now).total_seconds() * 1000),
        )
        return ingest_id, []

    device_ids = {item["device"] for item in points}
    metric_codes = {item["metric_code"] for item in points}
    devices = {
        str(device.pk): device
        for device in Device.objects.select_related("organization", "data_center").filter(pk__in=device_ids)
    }
    metrics = {
        metric.code: metric
        for metric in MetricDefinition.objects.filter(code__in=metric_codes)
    }
    telemetry_rows = []
    latest_by_key = {}
    device_latest_times = {}

    for item in points:
        device = devices[str(item["device"])]
        metric = metrics[item["metric_code"]]
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

        telemetry_rows.append(TelemetryPoint(time=ts, ingest_id=ingest_id, **common))
        key = (device.pk, metric.pk)
        latest_by_key[key] = LatestTelemetry(
            device=device,
            metric=metric,
            last_seen_at=ts,
            **common,
        )
        previous_ts = device_latest_times.get(device.pk)
        if previous_ts is None or ts > previous_ts:
            device_latest_times[device.pk] = ts

    created = TelemetryPoint.objects.bulk_create(telemetry_rows, batch_size=1000)
    LatestTelemetry.objects.bulk_create(
        list(latest_by_key.values()),
        batch_size=1000,
        update_conflicts=True,
        update_fields=[
            "organization",
            "data_center",
            "value_float",
            "value_integer",
            "value_boolean",
            "value_text",
            "raw_value_text",
            "quality",
            "last_seen_at",
            "source",
            "updated_at",
        ],
        unique_fields=["device", "metric"],
    )

    latest_rows = LatestTelemetry.objects.select_related("device", "device__device_type", "metric").filter(
        device_id__in=device_ids,
        metric_id__in=[metric.pk for metric in metrics.values()],
    )
    latest_by_key = {(row.device_id, row.metric_id): row for row in latest_rows}
    telemetry_deltas = [
        telemetry_delta_from_latest(latest_by_key[(device.pk, metric.pk)], observed_at=ts)
        for item in points
        for device in [devices[str(item["device"])]]
        for metric in [metrics[item["metric_code"]]]
        for ts in [item.get("time") or now]
    ]

    current_statuses = dict(
        Device.objects.filter(pk__in=device_ids).values_list("pk", "status")
    )
    from apps.alerts.models import AlertEvent, AlertStatus
    offline_alert_devices = set(
        AlertEvent.objects.filter(
            device_id__in=device_ids,
            status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED],
            message__icontains="offline",
        ).values_list("device_id", flat=True)
    )
    device_status_updates = {}
    for device_id, observed_at in device_latest_times.items():
        previous_status = current_statuses.get(device_id)
        Device.objects.filter(pk=device_id).update(last_seen_at=observed_at, status=DeviceStatus.ONLINE)
        if str(previous_status or "").upper() != DeviceStatus.ONLINE or device_id in offline_alert_devices:
            device_status_updates[str(device_id)] = {
                "device": devices[str(device_id)],
                "previous_status": previous_status or devices[str(device_id)].status,
                "observed_at": observed_at,
            }

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


__all__ = ["ingest_points", "store_telemetry_point", "get_telemetry_history_rows", "resolve_history_plan"]
