from django.apps import apps
from django.db import transaction


# ============================================================
# APC AP8853 Rack PDU Trap OID Seed Script
# Vendor: Schneider Electric / APC
# Device Type: Rack PDU
# Device Model: AP8853
# Real trap model: traps.SNMPTrapOIDMapping
# ============================================================


VENDOR_NAME = "Schneider Electric / APC"
DEVICE_TYPE_NAME = "Rack PDU"
DEVICE_MODEL_KEYWORDS = ["AP8853", "Schneider Electric / APC AP8853", "APC AP8853 Metered Rack PDU"]


TRAPS = [
    {
        "trap_oid": "1.3.6.1.4.1.318.0.266",
        "event_code": "pdu_communication_established",
        "event_name": "PDU Communication Established",
        "severity": "Info",
        "message_template": "Rack PDU communication has been established.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.267",
        "event_code": "pdu_communication_lost",
        "event_name": "PDU Communication Lost",
        "severity": "Critical",
        "message_template": "Rack PDU communication has been lost.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.270",
        "event_code": "pdu_device_config_change",
        "event_name": "PDU Device Configuration Changed",
        "severity": "Info",
        "message_template": "Rack PDU device configuration has changed.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.282",
        "event_code": "pdu_phase_config_change",
        "event_name": "PDU Phase Configuration Changed",
        "severity": "Info",
        "message_template": "Rack PDU phase configuration has changed.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.283",
        "event_code": "pdu_cancel_pending_command",
        "event_name": "PDU Pending Command Cancelled",
        "severity": "Info",
        "message_template": "Rack PDU pending command has been cancelled.",
        "create_alert": False,
    },

    # Load traps
    {
        "trap_oid": "1.3.6.1.4.1.318.0.272",
        "event_code": "pdu_low_load",
        "event_name": "PDU Low Load",
        "severity": "Warning",
        "message_template": "Rack PDU low load condition has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.273",
        "event_code": "pdu_low_load_cleared",
        "event_name": "PDU Low Load Cleared",
        "severity": "Recovery",
        "message_template": "Rack PDU low load condition has been cleared.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.274",
        "event_code": "pdu_near_overload",
        "event_name": "PDU Near Overload",
        "severity": "Warning",
        "message_template": "Rack PDU near overload condition has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.275",
        "event_code": "pdu_near_overload_cleared",
        "event_name": "PDU Near Overload Cleared",
        "severity": "Recovery",
        "message_template": "Rack PDU near overload condition has been cleared.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.276",
        "event_code": "pdu_overload",
        "event_name": "PDU Overload",
        "severity": "Critical",
        "message_template": "Rack PDU overload condition has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.277",
        "event_code": "pdu_overload_cleared",
        "event_name": "PDU Overload Cleared",
        "severity": "Recovery",
        "message_template": "Rack PDU overload condition has been cleared.",
        "create_alert": False,
    },

    # Power supply traps
    {
        "trap_oid": "1.3.6.1.4.1.318.0.278",
        "event_code": "pdu_power_supply_1_fail",
        "event_name": "PDU Power Supply 1 Failed",
        "severity": "Critical",
        "message_template": "Rack PDU power supply 1 failure has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.279",
        "event_code": "pdu_power_supply_1_ok",
        "event_name": "PDU Power Supply 1 Restored",
        "severity": "Recovery",
        "message_template": "Rack PDU power supply 1 has been restored.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.280",
        "event_code": "pdu_power_supply_2_fail",
        "event_name": "PDU Power Supply 2 Failed",
        "severity": "Critical",
        "message_template": "Rack PDU power supply 2 failure has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.281",
        "event_code": "pdu_power_supply_2_ok",
        "event_name": "PDU Power Supply 2 Restored",
        "severity": "Recovery",
        "message_template": "Rack PDU power supply 2 has been restored.",
        "create_alert": False,
    },

    # Modern Rack PDU condition traps
    {
        "trap_oid": "1.3.6.1.4.1.318.0.750",
        "event_code": "rpdu_critical_condition",
        "event_name": "Rack PDU Critical Condition",
        "severity": "Critical",
        "message_template": "Rack PDU critical condition has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.751",
        "event_code": "rpdu_critical_condition_cleared",
        "event_name": "Rack PDU Critical Condition Cleared",
        "severity": "Recovery",
        "message_template": "Rack PDU critical condition has been cleared.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.752",
        "event_code": "rpdu_warning_condition",
        "event_name": "Rack PDU Warning Condition",
        "severity": "Warning",
        "message_template": "Rack PDU warning condition has been detected.",
        "create_alert": True,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.753",
        "event_code": "rpdu_warning_condition_cleared",
        "event_name": "Rack PDU Warning Condition Cleared",
        "severity": "Recovery",
        "message_template": "Rack PDU warning condition has been cleared.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.754",
        "event_code": "rpdu_informational_condition",
        "event_name": "Rack PDU Informational Condition",
        "severity": "Info",
        "message_template": "Rack PDU informational condition has been detected.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.4.1.318.0.755",
        "event_code": "rpdu_informational_condition_cleared",
        "event_name": "Rack PDU Informational Condition Cleared",
        "severity": "Info",
        "message_template": "Rack PDU informational condition has been cleared.",
        "create_alert": False,
    },

    # Test and security
    {
        "trap_oid": "1.3.6.1.4.1.318.0.636",
        "event_code": "apc_test_trap",
        "event_name": "APC Test Trap",
        "severity": "Info",
        "message_template": "APC test trap received from Rack PDU.",
        "create_alert": False,
    },
    {
        "trap_oid": "1.3.6.1.6.3.1.1.5.5",
        "event_code": "snmp_authentication_failure",
        "event_name": "SNMP Authentication Failure",
        "severity": "Warning",
        "message_template": "SNMP authentication failure detected. Invalid SNMP community or unauthorized SNMP access attempt.",
        "create_alert": True,
    },
]


def get_model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def get_field_names(model):
    return {field.name for field in model._meta.fields}


def find_by_name_or_code(model, name):
    fields = get_field_names(model)

    if "name" in fields:
        obj = model.objects.filter(name=name).first()
        if obj:
            return obj

    if "code" in fields:
        possible_codes = [
            name,
            name.upper().replace(" ", "_").replace("/", "_").replace("-", "_"),
            name.upper().replace(" ", "_").replace("/", "").replace("-", "_"),
        ]
        obj = model.objects.filter(code__in=possible_codes).first()
        if obj:
            return obj

    return None


def find_vendor():
    Vendor = get_model("devices", "Vendor")
    if not Vendor:
        raise RuntimeError("devices.Vendor model not found")

    obj = find_by_name_or_code(Vendor, VENDOR_NAME)
    if obj:
        return obj

    fields = get_field_names(Vendor)
    data = {}

    if "name" in fields:
        data["name"] = VENDOR_NAME
    if "code" in fields:
        data["code"] = "SCHNEIDER_APC"
    if "website" in fields:
        data["website"] = "https://www.apc.com"
    if "is_active" in fields:
        data["is_active"] = True

    return Vendor.objects.create(**data)


def find_device_type():
    DeviceType = get_model("devices", "DeviceType")
    if not DeviceType:
        raise RuntimeError("devices.DeviceType model not found")

    obj = find_by_name_or_code(DeviceType, DEVICE_TYPE_NAME)
    if obj:
        return obj

    fields = get_field_names(DeviceType)
    data = {}

    if "name" in fields:
        data["name"] = DEVICE_TYPE_NAME
    if "code" in fields:
        data["code"] = "RACK_PDU"
    if "description" in fields:
        data["description"] = "Rack Power Distribution Unit"
    if "is_active" in fields:
        data["is_active"] = True

    return DeviceType.objects.create(**data)


def find_device_model(vendor=None, device_type=None):
    DeviceModel = get_model("devices", "DeviceModel")
    if not DeviceModel:
        raise RuntimeError("devices.DeviceModel model not found")

    fields = get_field_names(DeviceModel)

    queryset = DeviceModel.objects.all()

    if "model_number" in fields:
        obj = queryset.filter(model_number="AP8853").first()
        if obj:
            return obj

    if "name" in fields:
        for keyword in DEVICE_MODEL_KEYWORDS:
            obj = queryset.filter(name__icontains=keyword).first()
            if obj:
                return obj

    data = {}

    if "vendor" in fields and vendor:
        data["vendor"] = vendor
    if "device_type" in fields and device_type:
        data["device_type"] = device_type
    if "name" in fields:
        data["name"] = "APC AP8853 Metered Rack PDU"
    if "model_number" in fields:
        data["model_number"] = "AP8853"
    if "description" in fields:
        data["description"] = (
            "APC by Schneider Electric AP8853 Metered Rack PDU used for rack-level "
            "power distribution monitoring. Supports device-level, phase-level, and "
            "bank-level SNMP monitoring. This model has 42 physical outlets, 1 phase, "
            "and 2 metered banks. Per-outlet switching and per-outlet metering are not "
            "supported on this device."
        )
    if "is_active" in fields:
        data["is_active"] = True

    return DeviceModel.objects.create(**data)


def normalize_choice_value(model, field_name, desired_value):
    """
    Handles severity values whether your model stores:
    - Critical / Warning / Info / Recovery
    - critical / warning / info / recovery
    - CRITICAL / WARNING / INFO / RECOVERY
    """
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        return desired_value

    choices = getattr(field, "choices", None)
    if not choices:
        return desired_value

    desired_normalized = str(desired_value).lower().replace(" ", "").replace("_", "")

    for db_value, display_value in choices:
        db_norm = str(db_value).lower().replace(" ", "").replace("_", "")
        display_norm = str(display_value).lower().replace(" ", "").replace("_", "")
        if desired_normalized in [db_norm, display_norm]:
            return db_value

    return desired_value


def build_mapping_payload(model, vendor, device_type, device_model, trap):
    fields = get_field_names(model)
    payload = {}

    if "vendor" in fields:
        payload["vendor"] = vendor
    if "device_type" in fields:
        payload["device_type"] = device_type
    if "device_model" in fields:
        payload["device_model"] = device_model

    if "trap_oid" in fields:
        payload["trap_oid"] = trap["trap_oid"]
    elif "oid" in fields:
        payload["oid"] = trap["trap_oid"]

    if "event_code" in fields:
        payload["event_code"] = trap["event_code"]
    elif "code" in fields:
        payload["code"] = trap["event_code"]

    if "event_name" in fields:
        payload["event_name"] = trap["event_name"]
    elif "name" in fields:
        payload["name"] = trap["event_name"]

    if "severity" in fields:
        payload["severity"] = normalize_choice_value(model, "severity", trap["severity"])

    if "message_template" in fields:
        payload["message_template"] = trap["message_template"]
    elif "message" in fields:
        payload["message"] = trap["message_template"]
    elif "description" in fields:
        payload["description"] = trap["message_template"]

    if "create_alert" in fields:
        payload["create_alert"] = trap["create_alert"]

    if "is_active" in fields:
        payload["is_active"] = True

    return payload


def get_lookup_kwargs(model, payload):
    fields = get_field_names(model)

    if "trap_oid" in fields:
        lookup = {"trap_oid": payload["trap_oid"]}
    elif "oid" in fields:
        lookup = {"oid": payload["oid"]}
    else:
        raise RuntimeError(
            f"{model._meta.label} must have either 'trap_oid' or 'oid' field"
        )

    # If your table allows same trap OID for different models/vendors,
    # include these foreign keys in lookup.
    if "vendor" in fields and "vendor" in payload:
        lookup["vendor"] = payload["vendor"]
    if "device_type" in fields and "device_type" in payload:
        lookup["device_type"] = payload["device_type"]
    if "device_model" in fields and "device_model" in payload:
        lookup["device_model"] = payload["device_model"]

    return lookup


def main():
    TrapOIDMapping = get_model("traps", "SNMPTrapOIDMapping")
    if not TrapOIDMapping:
        raise RuntimeError("Could not find traps.SNMPTrapOIDMapping model")

    vendor = find_vendor()
    device_type = find_device_type()
    device_model = find_device_model(vendor=vendor, device_type=device_type)

    created_count = 0
    updated_count = 0

    print("Using:")
    print("  Vendor:", vendor)
    print("  Device type:", device_type)
    print("  Device model:", device_model)
    print("  Trap model:", TrapOIDMapping._meta.label)
    print("")

    with transaction.atomic():
        for trap in TRAPS:
            payload = build_mapping_payload(
                TrapOIDMapping,
                vendor,
                device_type,
                device_model,
                trap,
            )

            lookup = get_lookup_kwargs(TrapOIDMapping, payload)

            defaults = payload.copy()
            for key in lookup:
                defaults.pop(key, None)

            obj, created = TrapOIDMapping.objects.update_or_create(
                **lookup,
                defaults=defaults,
            )

            if created:
                created_count += 1
                action = "CREATED"
            else:
                updated_count += 1
                action = "UPDATED"

            print(f"{action}: {trap['event_code']} - {trap['trap_oid']}")

    print("")
    print("Done.")
    print(f"Created: {created_count}")
    print(f"Updated: {updated_count}")
    print(f"Total:   {len(TRAPS)}")


main()
