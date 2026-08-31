import logging

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.devices.models import Device
from apps.alerts.services import create_or_update_trap_alert
from apps.telemetry.models import DeviceEvent
from apps.traps.models import SNMPTrapEvent, SNMPTrapOIDMapping, SNMPTrapSource, TrapResolutionSource
from apps.traps.services import get_mib_registry
from .pac_alarm_handler import is_pac_confirmation_trap, is_pac_device

logger = logging.getLogger(__name__)

UNMAPPED_TRAP_EVENT_CODE = "UNMAPPED_SNMP_TRAP"
UNMAPPED_TRAP_EVENT_NAME = "Unmapped SNMP Trap"


def _lookup_trap_mapping(device: Device, trap_oid: str):
    if not device:
        return None
    vendor = getattr(getattr(device, "device_model", None), "vendor", None)
    queryset = (
        SNMPTrapOIDMapping.objects.filter(device_type=device.device_type, trap_oid=trap_oid, is_active=True)
        .filter(models.Q(device_model=device.device_model) | models.Q(device_model__isnull=True))
        .filter(models.Q(vendor=vendor) | models.Q(vendor__isnull=True))
        .order_by("-device_model", "-vendor", "-created_at")
    )
    return queryset.first()


def _lookup_mib_definition(trap_oid: str):
    try:
        return get_mib_registry().resolve(trap_oid)
    except Exception:
        logger.exception("MIB fallback lookup failed trap_oid=%s", trap_oid)
        return None


def _severity_for_unmapped_trap():
    return str(getattr(settings, "SNMP_UNMAPPED_TRAP_SEVERITY", "WARNING") or "WARNING").strip().upper()


def _build_unmapped_message(*, trap_oid: str, mib_definition) -> str:
    if mib_definition:
        return (
            f"Unmapped SNMP trap received. OID {trap_oid} was resolved from "
            f"{mib_definition.module} as {mib_definition.symbol}. Review and configure a database trap mapping."
        )
    return f"Unmapped SNMP trap received. No matching MIB definition was found for OID {trap_oid}."


def _build_trap_payload(*, trap_oid: str, mib_definition, source_ip: str, raw_varbinds: dict, resolution_source: str) -> dict:
    payload = dict(raw_varbinds or {})
    payload.update(
        {
            "_resolution_source": resolution_source,
            "_canonical_trap_oid": trap_oid,
        }
    )
    if mib_definition:
        payload.update(
            {
                "_mib_module": mib_definition.module,
                "_mib_symbol": mib_definition.symbol,
                "_mib_description": mib_definition.description,
                "_mib_status": mib_definition.status,
            }
        )
    payload.setdefault("_transport_source_ip", source_ip)
    return payload


def _create_or_update_unmapped_review_alert(*, device: Device, trap_oid: str, message: str, mib_definition, triggered_at, raw_payload: dict):
    if not bool(getattr(settings, "SNMP_UNMAPPED_TRAP_CREATE_REVIEW_ALERT", True)):
        return None

    return create_or_update_trap_alert(
        device=device,
        event_code=UNMAPPED_TRAP_EVENT_CODE,
        event_name=UNMAPPED_TRAP_EVENT_NAME,
        severity=_severity_for_unmapped_trap(),
        message=message,
        triggered_at=triggered_at,
        trap_oid=trap_oid,
        raw_payload={
            **raw_payload,
            "review_alert": True,
            "resolution_source": TrapResolutionSource.MIB if mib_definition else TrapResolutionSource.UNKNOWN,
            "mib_module": getattr(mib_definition, "module", None),
            "mib_symbol": getattr(mib_definition, "symbol", None),
        },
    )


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

    mapping = _lookup_trap_mapping(device, trap_oid) if device else None
    mib_definition = None
    resolution_source = TrapResolutionSource.DATABASE if mapping else TrapResolutionSource.UNKNOWN
    if not mapping:
        mib_definition = _lookup_mib_definition(trap_oid)
        if mib_definition:
            resolution_source = TrapResolutionSource.MIB

    received_at = timezone.now()
    if mapping:
        message = mapping.message_template if mapping.message_template else mapping.event_name
    else:
        message = _build_unmapped_message(trap_oid=trap_oid, mib_definition=mib_definition)

    raw_payload = _build_trap_payload(
        trap_oid=trap_oid,
        mib_definition=mib_definition,
        source_ip=source_ip,
        raw_varbinds=raw_varbinds or {},
        resolution_source=resolution_source,
    )
    event = SNMPTrapEvent.objects.create(
        organization=organization,
        data_center=data_center,
        device=device,
        source_ip=source_ip,
        trap_oid=trap_oid or "UNKNOWN",
        event_code=mapping.event_code if mapping else (mib_definition.symbol if mib_definition else None),
        event_name=mapping.event_name if mapping else (mib_definition.symbol if mib_definition else "Unmapped SNMP Trap"),
        severity=mapping.severity if mapping else _severity_for_unmapped_trap(),
        resolution_source=resolution_source,
        mib_module=mib_definition.module if mib_definition else None,
        mib_symbol=mib_definition.symbol if mib_definition else None,
        mib_description=mib_definition.description if mib_definition else None,
        mib_status=mib_definition.status if mib_definition else "UNKNOWN",
        requires_mapping_review=not bool(mapping),
        raw_varbinds=raw_payload,
        message=message,
        received_at=received_at,
        is_mapped=bool(mapping),
        is_processed=True,
    )

    if device and not mapping:
        raw_payload = {
            **raw_payload,
            "_trap_review_event_code": UNMAPPED_TRAP_EVENT_CODE,
            "_trap_review_event_name": UNMAPPED_TRAP_EVENT_NAME,
        }
        DeviceEvent.objects.create(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            event_code=UNMAPPED_TRAP_EVENT_CODE,
            event_name=UNMAPPED_TRAP_EVENT_NAME,
            severity=_severity_for_unmapped_trap(),
            message=message,
            occurred_at=received_at,
            raw_payload=raw_payload,
        )
        _create_or_update_unmapped_review_alert(
            device=device,
            trap_oid=trap_oid,
            message=message,
            mib_definition=mib_definition,
            triggered_at=received_at,
            raw_payload=raw_payload,
        )

    if device and mapping:
        is_pac_confirmation = is_pac_device(device) and is_pac_confirmation_trap(mapping.event_code)
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
        # PAC trap codes only tell us something changed. The confirmation task polls
        # the alarm OIDs immediately so we can persist the exact alarm state.
        if mapping.create_alert and not is_pac_confirmation:
            create_or_update_trap_alert(
                device=device,
                event_code=mapping.event_code,
                event_name=mapping.event_name,
                severity=mapping.severity,
                message=message,
                triggered_at=received_at,
                trap_oid=trap_oid,
                raw_payload=raw_varbinds or {},
            )
        if mapping.resolves_event_code:
            from apps.alerts.models import AlertEvent, AlertStatus
            from apps.alerts.services.engine import resolve_alert

            active_alerts = AlertEvent.objects.filter(
                device=device,
                metadata__trap_event_code=mapping.resolves_event_code,
                status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED],
            )
            for active_alert in active_alerts:
                resolve_alert(
                    active_alert,
                    latest=None,
                    resolution_type="AUTO",
                    comment=f"Automatically resolved by clearing trap: {mapping.event_name} ({mapping.event_code})",
                )
        if is_pac_confirmation:
            from .tasks import process_pac_trap_alarm_confirmation_task

            transaction.on_commit(
                lambda: process_pac_trap_alarm_confirmation_task.delay(
                    device_id=str(device.pk),
                    trap_event_id=str(event.pk),
                    event_code=mapping.event_code,
                )
            )
    if not device:
        logger.info(
            "Stored SNMP trap from unknown source trap_oid=%s source_ip=%s resolution_source=%s",
            trap_oid,
            source_ip,
            resolution_source,
        )
    return event
