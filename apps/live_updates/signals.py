from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.alerts.models import AlertEvent, AlertEventLog
from apps.devices.models import Device
from apps.devices.services.overview_summary import bump_overview_summary_cache_version
from apps.telemetry.models import DeviceEvent, LatestTelemetry
from apps.traps.models import SNMPTrapEvent

from .services import publish_live_update

UPS_DEVICE_TYPE_CODE = "UPS"


def _is_ups_device(device) -> bool:
    device_type = getattr(device, "device_type", None)
    if device_type is None:
        return False
    return str(getattr(device_type, "code", "")).strip().upper() == UPS_DEVICE_TYPE_CODE


def _device_scopes(device) -> list[str]:
    device_id = getattr(device, "pk", None)
    if not device_id:
        return ["overview"]
    scopes = ["overview", f"device:{device_id}"]
    organization_id = getattr(device, "organization_id", None)
    data_center_id = getattr(device, "data_center_id", None)
    if organization_id:
        scopes.append(f"organization:{organization_id}")
    if data_center_id:
        scopes.append(f"data_center:{data_center_id}")
    device_type = getattr(device, "device_type", None)
    device_type_code = str(getattr(device_type, "code", "") or "").strip().upper()
    if device_type_code:
        scopes.append(f"device_type:{device_type_code}")
    return scopes


@receiver(post_save, sender=AlertEvent)
def alert_event_saved(sender, instance, created, **kwargs):
    bump_overview_summary_cache_version()
    if not _is_ups_device(instance.device):
        return
    publish_live_update(
        event_type="alert_event",
        resource_type="AlertEvent",
        resource_id=instance.pk,
        scopes=["alerts", *_device_scopes(instance.device)],
        metadata={
            "created": created,
            "status": instance.status,
            "device_id": str(instance.device_id) if instance.device_id else None,
        },
    )


@receiver(post_delete, sender=AlertEvent)
def alert_event_deleted(sender, instance, **kwargs):
    bump_overview_summary_cache_version()
    if not _is_ups_device(instance.device):
        return
    publish_live_update(
        event_type="alert_event_deleted",
        resource_type="AlertEvent",
        resource_id=instance.pk,
        scopes=["alerts", *_device_scopes(instance.device)],
        metadata={"device_id": str(instance.device_id) if instance.device_id else None},
    )


@receiver(post_save, sender=AlertEventLog)
def alert_event_log_saved(sender, instance, created, **kwargs):
    if not instance.alert_event_id or not _is_ups_device(instance.alert_event.device):
        return
    publish_live_update(
        event_type="alert_event_log",
        resource_type="AlertEventLog",
        resource_id=instance.pk,
        scopes=["alerts", *_device_scopes(instance.alert_event.device)],
        metadata={"created": created, "alert_event_id": str(instance.alert_event_id)},
    )


@receiver(post_save, sender=DeviceEvent)
def device_event_saved(sender, instance, created, **kwargs):
    if not _is_ups_device(instance.device):
        return
    publish_live_update(
        event_type="device_event",
        resource_type="DeviceEvent",
        resource_id=instance.pk,
        scopes=_device_scopes(instance.device),
        metadata={"created": created, "device_id": str(instance.device_id) if instance.device_id else None},
    )


@receiver(post_save, sender=SNMPTrapEvent)
def snmp_trap_event_saved(sender, instance, created, **kwargs):
    if not _is_ups_device(instance.device):
        return
    publish_live_update(
        event_type="snmp_trap_event",
        resource_type="SNMPTrapEvent",
        resource_id=instance.pk,
        scopes=_device_scopes(instance.device),
        metadata={"created": created, "device_id": str(instance.device_id) if instance.device_id else None},
    )


@receiver(post_save, sender=LatestTelemetry)
@receiver(post_delete, sender=LatestTelemetry)
def latest_telemetry_changed(sender, instance, **kwargs):
    bump_overview_summary_cache_version()


@receiver(post_save, sender=Device)
@receiver(post_delete, sender=Device)
def device_changed(sender, instance, **kwargs):
    publish_live_update(
        event_type="device_update",
        resource_type="Device",
        resource_id=instance.pk,
        scopes=_device_scopes(instance),
        metadata={"device_id": str(instance.pk)},
    )
