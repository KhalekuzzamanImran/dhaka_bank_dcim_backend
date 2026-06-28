from django.db import models, transaction
from django.utils import timezone

from apps.alerts.models import AlertEvent
from apps.devices.models import Device
from apps.telemetry.models import DeviceEvent
from apps.traps.models import SNMPTrapEvent, SNMPTrapOIDMapping, SNMPTrapSource


@transaction.atomic
def process_snmp_trap(*, source_ip, trap_oid, raw_varbinds):
    trap_source = (
        SNMPTrapSource.objects.select_related("organization", "data_center", "device", "device__device_type", "device__device_model")
        .filter(source_ip=source_ip, is_enabled=True)
        .first()
    )
    device = trap_source.device if trap_source else None
    organization = trap_source.organization if trap_source else None
    data_center = trap_source.data_center if trap_source else None

    if not device:
        device = (
            Device.objects.select_related("organization", "data_center", "device_type", "device_model", "device_model__vendor")
            .filter(ip_address=source_ip, is_active=True)
            .first()
        )
        if device:
            organization = device.organization
            data_center = device.data_center

    mapping = None
    if device:
        mapping = (
            SNMPTrapOIDMapping.objects.filter(device_type=device.device_type, trap_oid=trap_oid, is_active=True)
            .filter(models.Q(device_model=device.device_model) | models.Q(device_model__isnull=True))
            .order_by("-device_model")
            .first()
        )

    received_at = timezone.now()
    message = mapping.message_template if mapping and mapping.message_template else (mapping.event_name if mapping else "Unmapped SNMP trap received")
    event = SNMPTrapEvent.objects.create(
        organization=organization,
        data_center=data_center,
        device=device,
        source_ip=source_ip,
        trap_oid=trap_oid or "UNKNOWN",
        event_code=mapping.event_code if mapping else None,
        event_name=mapping.event_name if mapping else None,
        severity=mapping.severity if mapping else "INFO",
        raw_varbinds=raw_varbinds or {},
        message=message,
        received_at=received_at,
        is_mapped=bool(mapping),
        is_processed=True,
    )

    if device and mapping:
        DeviceEvent.objects.create(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            event_code=mapping.event_code,
            event_name=mapping.event_name,
            severity=mapping.severity,
            message=message,
            occurred_at=received_at,
            raw_payload=raw_varbinds or {},
        )
        if mapping.create_alert:
            AlertEvent.objects.create(
                organization=device.organization,
                data_center=device.data_center,
                device=device,
                metric=None,
                alert_rule=None,
                severity=mapping.severity,
                status="OPEN",
                message=message,
                triggered_at=received_at,
            )
    return event
