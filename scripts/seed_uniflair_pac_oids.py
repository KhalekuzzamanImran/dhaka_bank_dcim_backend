from decimal import Decimal
from django.apps import apps
from django.db import transaction

from apps.devices.models import DeviceType, Vendor, DeviceModel
from apps.telemetry.models import MetricDefinition

SNMPOIDMapping = apps.get_model("devices", "SNMPOIDMapping")


# ============================================================
# Target device information
# ============================================================

DEVICE_TYPE_NAME = "PAC"
VENDOR_NAME = "Schneider Electric / Uniflair"
DEVICE_MODEL_NAME = "Uniflair AM LE UG40 DX"


# ============================================================
# Helper functions
# ============================================================

def normalize_choice(model, field_name, value):
    """
    Convert readable values like:
      Float   -> FLOAT
      Integer -> INTEGER
      Text    -> TEXT
      Cooling -> matching DB category value if choices exist
    """
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
    Also checks str(obj), because your admin page may show display text from __str__().
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

    for obj in model.objects.all():
        if str(obj).strip().lower() == str(value).strip().lower():
            return obj

    value_lower = str(value).strip().lower()
    for obj in model.objects.all():
        if value_lower in str(obj).strip().lower():
            return obj
        if str(obj).strip().lower() in value_lower:
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

    # OID changes must update the existing metric mapping rather than create a
    # second active mapping for the same device/model/metric.
    lookup = {
        "device_type": device_type,
        "vendor": vendor,
        "device_model": device_model,
        "metric": metric,
    }

    mapping, created = SNMPOIDMapping.objects.update_or_create(
        **lookup,
        defaults={**defaults, "oid": item["oid"]},
    )

    return mapping, created


# ============================================================
# Schneider Electric / Uniflair PAC metrics and OIDs
# ============================================================

PAC_OID_ITEMS = [
    # ========================================================
    # Environment
    # ========================================================
    {
        "code": "pac_room_temperature",
        "name": "PAC Room Temperature",
        "category": "Cooling",
        "data_type": "Float",
        "unit": "C",
        "description": "PAC room temperature. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.9839.2.1.2.1.0",
        "mapping_data_type": "float",
        "scale_factor": "0.1",
        "offset_value": "0",
    },
    {
        "code": "pac_room_humidity",
        "name": "PAC Room Humidity",
        "category": "Cooling",
        "data_type": "Float",
        "unit": "%",
        "description": "PAC room relative humidity. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.9839.2.1.2.6.0",
        "mapping_data_type": "float",
        "scale_factor": "0.1",
        "offset_value": "0",
    },

    # ========================================================
    # Operation
    # ========================================================
    {
        "code": "pac_running_status",
        "name": "PAC Running Status",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC system running status. Usually 1 means ON/running and 0 means OFF.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_dehumidification_status",
        "name": "PAC Dehumidification Status",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC dehumidification operating status from CAREL dehumidification.0.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.10.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_fan_speed",
        "name": "PAC Fan Speed",
        "category": "Cooling",
        "data_type": "Float",
        "unit": "%",
        "description": "PAC fan speed percentage. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.9839.2.1.2.18.0",
        "mapping_data_type": "float",
        "scale_factor": "0.1",
        "offset_value": "0",
    },
    {
        "code": "pac_cooling_setpoint",
        "name": "PAC Cooling Setpoint",
        "category": "Cooling",
        "data_type": "Float",
        "unit": "C",
        "description": "PAC cooling setpoint. Raw value is divided by 10.",
        "oid": "1.3.6.1.4.1.9839.2.1.2.20.0",
        "mapping_data_type": "float",
        "scale_factor": "0.1",
        "offset_value": "0",
    },
    {
        "code": "pac_compressor_1_status",
        "name": "PAC Compressor 1 Status",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC compressor 1 status. Usually 1 means active and 0 means inactive.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.2.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_compressor_2_status",
        "name": "PAC Compressor 2 Status",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC compressor 2 status. Usually 1 means active and 0 means inactive.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.3.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_compressor_3_status",
        "name": "PAC Compressor 3 Status",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC compressor 3 status. Usually 1 means active and 0 means inactive.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.4.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_compressor_4_status",
        "name": "PAC Compressor 4 Status",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC compressor 4 status. Usually 1 means active and 0 means inactive.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.5.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },

    # ========================================================
    # Alarms
    # ========================================================
    {
        "code": "pac_general_alarm",
        "name": "PAC General Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC general alarm status. Usually 1 means alarm active and 0 means normal.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.66.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_room_high_temperature_alarm",
        "name": "PAC Room High Temperature Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC high room temperature alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.21.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_room_low_temperature_alarm",
        "name": "PAC Room Low Temperature Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC low room temperature alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.22.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_room_high_humidity_alarm",
        "name": "PAC Room High Humidity Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC high room humidity alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.23.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_room_low_humidity_alarm",
        "name": "PAC Room Low Humidity Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC low room humidity alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.24.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_airflow_alarm",
        "name": "PAC Airflow Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC airflow alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.28.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_water_leak_alarm",
        "name": "PAC Water Leak Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC water leak or flood alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.27.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_filter_alarm",
        "name": "PAC Filter Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC filter alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.26.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_smoke_fire_alarm",
        "name": "PAC Smoke Fire Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC smoke or fire alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.37.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_emergency_power_alarm",
        "name": "PAC Emergency Power Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC emergency alarm status.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.12.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_phase_sequence_alarm",
        "name": "PAC Phase Sequence Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC phase sequence alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.36.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_lan_alarm",
        "name": "PAC LAN Alarm",
        "category": "Alarm",
        "data_type": "Integer",
        "unit": "",
        "description": "PAC LAN communication alarm.",
        "oid": "1.3.6.1.4.1.9839.2.1.1.38.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },

    # ========================================================
    # Maintenance
    # ========================================================
    {
        "code": "pac_unit_work_hours",
        "name": "PAC Unit Work Hours",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "h",
        "description": "PAC total unit working hours.",
        "oid": "1.3.6.1.4.1.9839.2.1.3.2.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
    },
    {
        "code": "pac_filter_work_hours",
        "name": "PAC Filter Work Hours",
        "category": "Cooling",
        "data_type": "Integer",
        "unit": "h",
        "description": "PAC filter working hours.",
        "oid": "1.3.6.1.4.1.9839.2.1.3.1.0",
        "mapping_data_type": "integer",
        "scale_factor": "1",
        "offset_value": "0",
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

    for item in PAC_OID_ITEMS:
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

print("PAC metrics and vendor/model-specific SNMP OID mappings imported successfully.")
print(f"Device type     : {DEVICE_TYPE_NAME}")
print(f"Vendor          : {VENDOR_NAME}")
print(f"Device model    : {device_model}")
print(f"Metrics created : {metric_created_count}")
print(f"Metrics updated : {metric_updated_count}")
print(f"Mappings created: {mapping_created_count}")
print(f"Mappings updated: {mapping_updated_count}")
print(f"Total metrics   : {len(PAC_OID_ITEMS)}")
