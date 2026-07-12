from decimal import Decimal
from django.apps import apps
from django.db import transaction

from apps.devices.models import DeviceType, Vendor, DeviceModel
from apps.telemetry.models import MetricDefinition

SNMPOIDMapping = apps.get_model("devices", "SNMPOIDMapping")


# ============================================================
# Target device information
# ============================================================

DEVICE_TYPE_NAME = "UPS"
VENDOR_NAME = "Schneider Electric / APC"
DEVICE_MODEL_NAME = "InfraStruXure Symmetra 160k"

# ============================================================
# Helper functions
# ============================================================

def normalize_choice(model, field_name, value):
    field = model._meta.get_field(field_name)
    choices = list(getattr(field, "choices", []) or [])

    if not choices:
        return value

    value_lower = str(value).strip().lower()

    for choice_value, choice_label in choices:
        if str(choice_value).strip().lower() == value_lower:
            return choice_value
        if str(choice_label).strip().lower() == value_lower:
            return choice_value

    valid = ", ".join(
        [f"{choice_value} ({choice_label})" for choice_value, choice_label in choices]
    )
    raise ValueError(
        f"Invalid choice '{value}' for {model.__name__}.{field_name}. "
        f"Valid choices: {valid}"
    )


def get_required_object(model, value):
    """
    Finds an object by common fields: name, code, model_number.
    Raises error if not found.
    """
    search_fields = ["name", "code", "model_number"]

    for field_name in search_fields:
        try:
            model._meta.get_field(field_name)
        except Exception:
            continue

        obj = model.objects.filter(**{f"{field_name}__iexact": value}).first()
        if obj:
            return obj

    available = []
    for field_name in search_fields:
        try:
            model._meta.get_field(field_name)
        except Exception:
            continue

        available = list(
            model.objects.values_list(field_name, flat=True).order_by(field_name)[:20]
        )
        if available:
            break

    raise ValueError(
        f"{model.__name__} not found: {value}. "
        f"Please check spelling in admin. Available examples: {available}"
    )


def create_or_update_metric(item):
    data_type = normalize_choice(
        MetricDefinition,
        "data_type",
        item["data_type"],
    )

    try:
        category = normalize_choice(
            MetricDefinition,
            "category",
            item["category"],
        )
    except Exception:
        category = item["category"]

    defaults = {
        "name": item["name"],
        "category": category,
        "data_type": data_type,
        "unit": item.get("unit", "") or "",
        "description": item.get("description", "") or "",
        "is_active": True,
    }

    metric, created = MetricDefinition.objects.update_or_create(
        code=item["code"],
        defaults=defaults,
    )

    return metric, created


def create_or_update_oid_mapping(item, metric, device_type, vendor, device_model):
    mapping_data_type = normalize_choice(
        SNMPOIDMapping,
        "data_type",
        item["mapping_data_type"],
    )

    defaults = {
        "data_type": mapping_data_type,
        "scale_factor": Decimal(str(item.get("scale_factor", "1"))),
        "offset_value": Decimal(str(item.get("offset_value", "0"))),
        "is_active": True,
    }

    lookup = {
        "device_type": device_type,
        "vendor": vendor,
        "device_model": device_model,
        "metric": metric,
        "oid": item["oid"],
    }

    mapping, created = SNMPOIDMapping.objects.update_or_create(
        **lookup,
        defaults=defaults,
    )

    return mapping, created


# ============================================================
# Important APC / Schneider Symmetra UPS metrics and OIDs
# ============================================================

UPS_IMPORTANT_OIDS = [
    {
        "code": "ups_output_status",
        "name": "UPS Output Status",
        "category": "Status",
        "data_type": "Integer",
        "unit": "",
        "description": "UPS output status. Example values: online, on battery, bypass, off.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.1.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_comm_status",
        "name": "UPS Communication Status",
        "category": "Status",
        "data_type": "Integer",
        "unit": "",
        "description": "UPS communication status.",
        "oid": "1.3.6.1.4.1.318.1.1.1.8.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_battery_status",
        "name": "UPS Battery Status",
        "category": "Battery",
        "data_type": "Integer",
        "unit": "",
        "description": "UPS battery status.",
        "oid": "1.3.6.1.4.1.318.1.1.1.2.1.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_battery_capacity_percent",
        "name": "UPS Battery Capacity Percent",
        "category": "Battery",
        "data_type": "Float",
        "unit": "%",
        "description": "UPS battery capacity percentage. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.2.3.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_battery_runtime_remaining",
        "name": "UPS Battery Runtime Remaining",
        "category": "Battery",
        "data_type": "Integer",
        "unit": "timeticks",
        "description": "Estimated UPS battery runtime remaining.",
        "oid": "1.3.6.1.4.1.318.1.1.1.2.2.3.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_battery_temperature_celsius",
        "name": "UPS Battery Temperature",
        "category": "Battery",
        "data_type": "Float",
        "unit": "C",
        "description": "UPS battery temperature in Celsius. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.2.3.2.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_bad_battery_pack_count",
        "name": "UPS Bad Battery Pack Count",
        "category": "Battery",
        "data_type": "Integer",
        "unit": "count",
        "description": "Number of bad battery packs.",
        "oid": "1.3.6.1.4.1.318.1.1.1.2.2.6.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_input_voltage",
        "name": "UPS Input Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS input voltage. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.3.3.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_input_frequency",
        "name": "UPS Input Frequency",
        "category": "Power",
        "data_type": "Float",
        "unit": "Hz",
        "description": "UPS input frequency. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.3.3.4.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_input_line_fail_cause",
        "name": "UPS Input Line Fail Cause",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "UPS input line failure cause.",
        "oid": "1.3.6.1.4.1.318.1.1.1.3.2.5.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_voltage",
        "name": "UPS Output Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS output voltage. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.3.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_frequency",
        "name": "UPS Output Frequency",
        "category": "Power",
        "data_type": "Float",
        "unit": "Hz",
        "description": "UPS output frequency. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.3.2.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_load_percent",
        "name": "UPS Load Percent",
        "category": "Power",
        "data_type": "Float",
        "unit": "%",
        "description": "UPS output load percentage. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.3.3.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_current",
        "name": "UPS Output Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS output current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.3.4.0",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_redundancy",
        "name": "UPS Output Redundancy",
        "category": "Power",
        "data_type": "Integer",
        "unit": "",
        "description": "UPS output redundancy level.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.2.5.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_kva_capacity",
        "name": "UPS Output kVA Capacity",
        "category": "Power",
        "data_type": "Integer",
        "unit": "kVA",
        "description": "UPS output kVA capacity.",
        "oid": "1.3.6.1.4.1.318.1.1.1.4.2.6.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_abnormal_conditions",
        "name": "UPS Abnormal Conditions",
        "category": "Alarm",
        "data_type": "Text",
        "unit": "",
        "description": "UPS abnormal conditions bitmask.",
        "oid": "1.3.6.1.4.1.318.1.1.1.11.2.1.0",
        "mapping_data_type": "string",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_l1_voltage",
        "name": "UPS Output L1 Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS output phase L1 voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.3.1.1.1",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_l2_voltage",
        "name": "UPS Output L2 Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS output phase L2 voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.3.1.1.2",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_l3_voltage",
        "name": "UPS Output L3 Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS output phase L3 voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.3.1.1.3",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_l1_current",
        "name": "UPS Output L1 Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS output phase L1 current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.4.1.1.1",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_l2_current",
        "name": "UPS Output L2 Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS output phase L2 current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.4.1.1.2",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_l3_current",
        "name": "UPS Output L3 Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS output phase L3 current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.4.1.1.3",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_l1_percent_load",
        "name": "UPS Output L1 Percent Load",
        "category": "Power",
        "data_type": "Float",
        "unit": "%",
        "description": "UPS output phase L1 load percentage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.10.1.1.1",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_l2_percent_load",
        "name": "UPS Output L2 Percent Load",
        "category": "Power",
        "data_type": "Float",
        "unit": "%",
        "description": "UPS output phase L2 load percentage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.10.1.1.2",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_output_l3_percent_load",
        "name": "UPS Output L3 Percent Load",
        "category": "Power",
        "data_type": "Float",
        "unit": "%",
        "description": "UPS output phase L3 load percentage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.3.3.1.10.1.1.3",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
        {
        "code": "ups_input_l1_voltage",
        "name": "UPS Input L1 Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS input phase L1 voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.2.3.1.3.1.1.1",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_input_l2_voltage",
        "name": "UPS Input L2 Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS input phase L2 voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.2.3.1.3.1.1.2",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_input_l3_voltage",
        "name": "UPS Input L3 Voltage",
        "category": "Power",
        "data_type": "Float",
        "unit": "V",
        "description": "UPS input phase L3 voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.2.3.1.3.1.1.3",
        "mapping_data_type": "integer",
        "scale_factor": "1",
    },
    {
        "code": "ups_input_l1_current",
        "name": "UPS Input L1 Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS input phase L1 current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.2.3.1.6.1.1.1",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_input_l2_current",
        "name": "UPS Input L2 Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS input phase L2 current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.2.3.1.6.1.1.2",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_input_l3_current",
        "name": "UPS Input L3 Current",
        "category": "Power",
        "data_type": "Float",
        "unit": "A",
        "description": "UPS input phase L3 current. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.318.1.1.1.9.2.3.1.6.1.1.3",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
]


# ============================================================
# Insert / update
# ============================================================

with transaction.atomic():
    device_type = get_required_object(DeviceType, DEVICE_TYPE_NAME)
    vendor = get_required_object(Vendor, VENDOR_NAME)
    device_model = get_required_object(DeviceModel, DEVICE_MODEL_NAME)

    metric_created_count = 0
    metric_updated_count = 0
    mapping_created_count = 0
    mapping_updated_count = 0

    for item in UPS_IMPORTANT_OIDS:
        metric, metric_created = create_or_update_metric(item)

        if metric_created:
            metric_created_count += 1
        else:
            metric_updated_count += 1

        mapping, mapping_created = create_or_update_oid_mapping(
            item=item,
            metric=metric,
            device_type=device_type,
            vendor=vendor,
            device_model=device_model,
        )

        if mapping_created:
            mapping_created_count += 1
        else:
            mapping_updated_count += 1

print("Important UPS metrics and vendor/model-specific SNMP OID mappings imported successfully.")
print(f"Device type     : {DEVICE_TYPE_NAME}")
print(f"Vendor          : {VENDOR_NAME}")
print(f"Device model    : {DEVICE_MODEL_NAME}")
print(f"Metrics created : {metric_created_count}")
print(f"Metrics updated : {metric_updated_count}")
print(f"Mappings created: {mapping_created_count}")
print(f"Mappings updated: {mapping_updated_count}")
print(f"Total metrics   : {len(UPS_IMPORTANT_OIDS)}")
