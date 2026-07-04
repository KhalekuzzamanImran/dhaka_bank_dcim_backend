from django.apps import apps
from django.db import transaction

from apps.devices.models import DeviceType, Vendor, DeviceModel
from apps.traps.models import SNMPTrapOIDMapping, TrapSeverity


DEVICE_TYPE_NAME = "UPS"
VENDOR_NAME = "Schneider Electric / APC"
DEVICE_MODEL_NAME = "InfraStruXure Symmetra 160k"


def get_required_object(model, value):
    search_fields = ["name", "code", "model_number"]

    for field_name in search_fields:
        try:
            model._meta.get_field(field_name)
        except Exception:
            continue

        obj = model.objects.filter(**{f"{field_name}__iexact": value}).first()
        if obj:
            return obj

    for obj in model.objects.all():
        if str(obj).strip().lower() == str(value).strip().lower():
            return obj

    value_lower = str(value).strip().lower()
    for obj in model.objects.all():
        obj_str = str(obj).strip().lower()
        if value_lower in obj_str or obj_str in value_lower:
            return obj

    available = []
    for field_name in search_fields:
        try:
            model._meta.get_field(field_name)
        except Exception:
            continue

        available = list(
            model.objects.values_list(field_name, flat=True).order_by(field_name)[:30]
        )
        if available:
            break

    raise ValueError(
        f"{model.__name__} not found: {value}. Available examples: {available}"
    )


TOP_10_UPS_TRAPS = [
    {
        "trap_oid": "1.3.6.1.4.1.318.0.1",
        "event_code": "ups_communication_lost",
        "event_name": "UPS Communication Lost",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "Communication with the UPS has been lost.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.2",
        "event_code": "ups_overload",
        "event_name": "UPS Overload",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS load is greater than rated capacity.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.3",
        "event_code": "ups_diagnostics_failed",
        "event_name": "UPS Diagnostics Failed",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS internal diagnostic self-test failed.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.4",
        "event_code": "ups_discharged",
        "event_name": "UPS Batteries Discharged",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS batteries are discharged.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.5",
        "event_code": "ups_on_battery",
        "event_name": "UPS On Battery",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS has switched to battery backup power.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.7",
        "event_code": "ups_low_battery",
        "event_name": "UPS Low Battery",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS battery is low and backup time is almost finished.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.17",
        "event_code": "ups_battery_needs_replacement",
        "event_name": "UPS Battery Needs Replacement",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS battery requires replacement.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.20",
        "event_code": "ups_hardware_failure_bypass",
        "event_name": "UPS Hardware Failure Bypass",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS is on bypass due to internal hardware failure.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.53",
        "event_code": "ups_battery_over_temperature",
        "event_name": "UPS Battery Over Temperature",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS battery temperature is above safe threshold.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.77",
        "event_code": "ups_abnormal_condition",
        "event_name": "UPS Abnormal Condition",
        "severity": TrapSeverity.CRITICAL,
        "message_template": "UPS abnormal condition has been detected.",
        "create_alert": True,
    },
]


def upsert_trap_mapping(item, device_type, vendor, device_model):
    mapping, created = SNMPTrapOIDMapping.objects.update_or_create(
        device_type=device_type,
        vendor=vendor,
        device_model=device_model,
        trap_oid=item["trap_oid"],
        defaults={
            "event_code": item["event_code"],
            "event_name": item["event_name"],
            "severity": item["severity"],
            "message_template": item["message_template"],
            "create_alert": item["create_alert"],
            "is_active": True,
        },
    )

    return mapping, created


with transaction.atomic():
    device_type = get_required_object(DeviceType, DEVICE_TYPE_NAME)
    vendor = get_required_object(Vendor, VENDOR_NAME)
    device_model = get_required_object(DeviceModel, DEVICE_MODEL_NAME)

    created_count = 0
    updated_count = 0

    for item in TOP_10_UPS_TRAPS:
        mapping, created = upsert_trap_mapping(
            item=item,
            device_type=device_type,
            vendor=vendor,
            device_model=device_model,
        )

        if created:
            created_count += 1
        else:
            updated_count += 1

print("Top 10 UPS trap OID mappings imported successfully.")
print(f"Device type      : {DEVICE_TYPE_NAME}")
print(f"Vendor           : {VENDOR_NAME}")
print(f"Device model     : {device_model}")
print(f"Mappings created : {created_count}")
print(f"Mappings updated : {updated_count}")
print(f"Total mappings   : {len(TOP_10_UPS_TRAPS)}")
