"""
Seed MetricDefinition and SNMPOIDMapping rows for APC AP8853 Rack PDU.

Run from project root:

    docker compose exec -T api python manage.py shell < seed_apc_ap8853_pdu_metrics.py

This script is idempotent. It updates existing metric definitions / OID mappings
when the same metric code or metric+OID mapping already exists.
"""

from decimal import Decimal
from django.apps import apps
from django.db import transaction

Vendor = apps.get_model("devices", "Vendor")
DeviceType = apps.get_model("devices", "DeviceType")
DeviceModel = apps.get_model("devices", "DeviceModel")
MetricDefinition = apps.get_model("telemetry", "MetricDefinition")
SNMPOIDMapping = apps.get_model("devices", "SNMPOIDMapping")

VENDOR_NAME = "Schneider Electric / APC"
VENDOR_CODE = "SCHNEIDER_APC"
DEVICE_TYPE_NAME = "Rack PDU"
DEVICE_TYPE_CODE = "RACK_PDU"
DEVICE_MODEL_NAME = "APC AP8853 Metered Rack PDU"
DEVICE_MODEL_NUMBER = "AP8853"


def set_if_field(obj, field_name, value):
    if hasattr(obj, field_name):
        setattr(obj, field_name, value)


def get_or_create_vendor():
    vendor = None
    for lookup in ({"code": VENDOR_CODE}, {"name": VENDOR_NAME}):
        try:
            vendor = Vendor.objects.filter(**lookup).first()
        except Exception:
            vendor = None
        if vendor:
            break
    if not vendor:
        kwargs = {"name": VENDOR_NAME}
        if any(f.name == "code" for f in Vendor._meta.fields):
            kwargs["code"] = VENDOR_CODE
        vendor = Vendor.objects.create(**kwargs)
    set_if_field(vendor, "name", VENDOR_NAME)
    set_if_field(vendor, "code", VENDOR_CODE)
    set_if_field(vendor, "website", "https://www.apc.com")
    vendor.save()
    return vendor


def get_or_create_device_type():
    device_type = None
    for lookup in ({"code": DEVICE_TYPE_CODE}, {"name": DEVICE_TYPE_NAME}):
        try:
            device_type = DeviceType.objects.filter(**lookup).first()
        except Exception:
            device_type = None
        if device_type:
            break
    if not device_type:
        kwargs = {"name": DEVICE_TYPE_NAME}
        if any(f.name == "code" for f in DeviceType._meta.fields):
            kwargs["code"] = DEVICE_TYPE_CODE
        device_type = DeviceType.objects.create(**kwargs)
    set_if_field(device_type, "name", DEVICE_TYPE_NAME)
    set_if_field(device_type, "code", DEVICE_TYPE_CODE)
    device_type.save()
    return device_type


def get_or_create_device_model(vendor, device_type):
    model = None
    for lookup in ({"model_number": DEVICE_MODEL_NUMBER}, {"name": DEVICE_MODEL_NAME}):
        try:
            model = DeviceModel.objects.filter(**lookup).first()
        except Exception:
            model = None
        if model:
            break
    if not model:
        kwargs = {
            "vendor": vendor,
            "device_type": device_type,
            "name": DEVICE_MODEL_NAME,
            "model_number": DEVICE_MODEL_NUMBER,
        }
        model = DeviceModel.objects.create(**kwargs)
    set_if_field(model, "vendor", vendor)
    set_if_field(model, "device_type", device_type)
    set_if_field(model, "name", DEVICE_MODEL_NAME)
    set_if_field(model, "model_number", DEVICE_MODEL_NUMBER)
    set_if_field(
        model,
        "description",
        "APC by Schneider Electric AP8853 Metered Rack PDU used for rack-level power distribution monitoring. "
        "Supports device-level, phase-level, and bank-level SNMP monitoring including voltage, current, "
        "active power, apparent power, power factor, total energy, load state, bank current, bank thresholds, "
        "power supply status, and device identity. This model has 42 physical outlets, 1 phase, and 2 metered banks. "
        "Per-outlet switching and per-outlet metering are not supported on this device.",
    )
    model.save()
    return model


# MetricDefinition rows plus SNMPOIDMapping rows.
# scale_factor and offset_value are applied by the poller after reading raw SNMP value.
METRICS = [
    # Identity / inventory
    {"code": "pdu_device_name", "name": "PDU Device Name", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU configured device name.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.3.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_location", "name": "PDU Location", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU configured location.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.4.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_hardware_revision", "name": "PDU Hardware Revision", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU hardware revision.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.5.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_firmware_version", "name": "PDU Firmware Version", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU firmware version.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.6.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_manufacture_date", "name": "PDU Manufacture Date", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU manufacture date.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.7.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_model_number", "name": "PDU Model Number", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU model number.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.8.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_serial_number", "name": "PDU Serial Number", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU serial number.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.9.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_contact", "name": "PDU Contact", "category": "Inventory", "data_type": "string", "unit": "", "description": "Rack PDU configured contact.", "oid": "1.3.6.1.4.1.318.1.1.26.2.1.10.1", "map_type": "string", "scale": "1", "offset": "0"},

    # Device properties
    {"code": "pdu_total_outlets", "name": "PDU Total Outlets", "category": "Power", "data_type": "integer", "unit": "count", "description": "Total physical outlets on the Rack PDU.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.4.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_switched_outlet_count", "name": "PDU Switched Outlet Count", "category": "Power", "data_type": "integer", "unit": "count", "description": "Number of switched outlets. AP8853 returns 0.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.5.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_metered_outlet_count", "name": "PDU Metered Outlet Count", "category": "Power", "data_type": "integer", "unit": "count", "description": "Number of metered outlets. AP8853 returns 0.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.6.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_count", "name": "PDU Phase Count", "category": "Power", "data_type": "integer", "unit": "count", "description": "Number of phases monitored by the Rack PDU.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.7.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank_count", "name": "PDU Metered Bank Count", "category": "Power", "data_type": "integer", "unit": "count", "description": "Number of metered banks monitored by the Rack PDU.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.8.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_max_current_rating", "name": "PDU Max Current Rating", "category": "Power", "data_type": "float", "unit": "A", "description": "Maximum current rating of the Rack PDU.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.9.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_max_phase_current_rating", "name": "PDU Max Phase Current Rating", "category": "Power", "data_type": "float", "unit": "A", "description": "Maximum current rating per phase.", "oid": "1.3.6.1.4.1.318.1.1.26.4.2.1.12.1", "map_type": "integer", "scale": "1", "offset": "0"},

    # Device status
    {"code": "pdu_device_load_state", "name": "PDU Device Load State", "category": "Power", "data_type": "integer", "unit": "state", "description": "Device load state. Normal value is 2 in rPDU2 status.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.4.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_active_power_kw", "name": "PDU Active Power", "category": "Power", "data_type": "float", "unit": "kW", "description": "Rack PDU active power. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.5.1", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_peak_power_kw", "name": "PDU Peak Power", "category": "Power", "data_type": "float", "unit": "kW", "description": "Rack PDU peak power. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.6.1", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_peak_power_timestamp", "name": "PDU Peak Power Timestamp", "category": "Power", "data_type": "string", "unit": "", "description": "Timestamp when peak power was recorded.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.7.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_total_energy", "name": "PDU Total Energy", "category": "Energy", "data_type": "float", "unit": "kWh", "description": "Rack PDU total energy counter. Energy today must be calculated from historical delta.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.9.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_energy_start_time", "name": "PDU Energy Start Time", "category": "Energy", "data_type": "string", "unit": "", "description": "Timestamp when energy counter started.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.10.1", "map_type": "string", "scale": "1", "offset": "0"},
    {"code": "pdu_command_pending", "name": "PDU Command Pending", "category": "Status", "data_type": "integer", "unit": "state", "description": "Command pending status for Rack PDU.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.11.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_power_supply_alarm", "name": "PDU Power Supply Alarm", "category": "Status", "data_type": "integer", "unit": "state", "description": "Rack PDU power supply alarm status.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.12.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_power_supply_1_status", "name": "PDU Power Supply 1 Status", "category": "Status", "data_type": "integer", "unit": "state", "description": "Rack PDU power supply 1 status.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.13.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_power_supply_2_status", "name": "PDU Power Supply 2 Status", "category": "Status", "data_type": "integer", "unit": "state", "description": "Rack PDU power supply 2 status.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.14.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_apparent_power_kva", "name": "PDU Apparent Power", "category": "Power", "data_type": "float", "unit": "kVA", "description": "Rack PDU apparent power. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.16.1", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_power_factor", "name": "PDU Power Factor", "category": "Power", "data_type": "float", "unit": "ratio", "description": "Rack PDU power factor. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.4.3.1.17.1", "map_type": "integer", "scale": "0.01", "offset": "0"},

    # Phase config/status
    {"code": "pdu_phase_table_size", "name": "PDU Phase Table Size", "category": "Power", "data_type": "integer", "unit": "count", "description": "Number of phase rows available in rPDU2 phase table.", "oid": "1.3.6.1.4.1.318.1.1.26.5.0", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_low_threshold", "name": "PDU Phase Low Load Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Phase low load threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.6.1.1.5.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_near_overload_threshold", "name": "PDU Phase Near Overload Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Phase near overload current threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.6.1.1.6.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_overload_threshold", "name": "PDU Phase Overload Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Phase overload current threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.6.1.1.7.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_load_state", "name": "PDU Phase Load State", "category": "Power", "data_type": "integer", "unit": "state", "description": "Phase load state. Normal value is 2 in rPDU2 status.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.4.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_current", "name": "PDU Phase Current", "category": "Power", "data_type": "float", "unit": "A", "description": "Phase/input current. Raw value is divided by 10.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.5.1", "map_type": "integer", "scale": "0.1", "offset": "0"},
    {"code": "pdu_phase_voltage", "name": "PDU Phase Voltage", "category": "Power", "data_type": "float", "unit": "V", "description": "Phase/input voltage.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.6.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_phase_active_power_kw", "name": "PDU Phase Active Power", "category": "Power", "data_type": "float", "unit": "kW", "description": "Phase active power. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.7.1", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_phase_apparent_power_kva", "name": "PDU Phase Apparent Power", "category": "Power", "data_type": "float", "unit": "kVA", "description": "Phase apparent power. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.8.1", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_phase_power_factor", "name": "PDU Phase Power Factor", "category": "Power", "data_type": "float", "unit": "ratio", "description": "Phase power factor. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.9.1", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_phase_peak_current", "name": "PDU Phase Peak Current", "category": "Power", "data_type": "float", "unit": "A", "description": "Phase peak current. Raw value is divided by 10.", "oid": "1.3.6.1.4.1.318.1.1.26.6.3.1.10.1", "map_type": "integer", "scale": "0.1", "offset": "0"},

    # Bank config/status
    {"code": "pdu_bank_table_size", "name": "PDU Bank Table Size", "category": "Power", "data_type": "integer", "unit": "count", "description": "Number of metered bank rows available.", "oid": "1.3.6.1.4.1.318.1.1.26.7.0", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank1_low_threshold", "name": "PDU Bank 1 Low Load Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 1 low load threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.8.1.1.5.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank2_low_threshold", "name": "PDU Bank 2 Low Load Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 2 low load threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.8.1.1.5.2", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank1_near_overload_threshold", "name": "PDU Bank 1 Near Overload Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 1 near overload threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.8.1.1.6.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank2_near_overload_threshold", "name": "PDU Bank 2 Near Overload Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 2 near overload threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.8.1.1.6.2", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank1_overload_threshold", "name": "PDU Bank 1 Overload Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 1 overload threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.8.1.1.7.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank2_overload_threshold", "name": "PDU Bank 2 Overload Threshold", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 2 overload threshold.", "oid": "1.3.6.1.4.1.318.1.1.26.8.1.1.7.2", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank1_load_state", "name": "PDU Bank 1 Load State", "category": "Power", "data_type": "integer", "unit": "state", "description": "Bank 1 load state. Normal value is 2 in rPDU2 status.", "oid": "1.3.6.1.4.1.318.1.1.26.8.3.1.4.1", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank2_load_state", "name": "PDU Bank 2 Load State", "category": "Power", "data_type": "integer", "unit": "state", "description": "Bank 2 load state. Normal value is 2 in rPDU2 status.", "oid": "1.3.6.1.4.1.318.1.1.26.8.3.1.4.2", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_bank1_current", "name": "PDU Bank 1 Current", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 1 current. Raw value is divided by 10.", "oid": "1.3.6.1.4.1.318.1.1.26.8.3.1.5.1", "map_type": "integer", "scale": "0.1", "offset": "0"},
    {"code": "pdu_bank2_current", "name": "PDU Bank 2 Current", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 2 current. Raw value is divided by 10.", "oid": "1.3.6.1.4.1.318.1.1.26.8.3.1.5.2", "map_type": "integer", "scale": "0.1", "offset": "0"},
    {"code": "pdu_bank1_peak_current", "name": "PDU Bank 1 Peak Current", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 1 peak current. Raw value is divided by 10.", "oid": "1.3.6.1.4.1.318.1.1.26.8.3.1.6.1", "map_type": "integer", "scale": "0.1", "offset": "0"},
    {"code": "pdu_bank2_peak_current", "name": "PDU Bank 2 Peak Current", "category": "Power", "data_type": "float", "unit": "A", "description": "Bank 2 peak current. Raw value is divided by 10.", "oid": "1.3.6.1.4.1.318.1.1.26.8.3.1.6.2", "map_type": "integer", "scale": "0.1", "offset": "0"},

    # Outlet capability summary only. Per-outlet live values are not supported by AP8853.
    {"code": "pdu_outlet_switched_table_size", "name": "PDU Outlet Switched Table Size", "category": "Power", "data_type": "integer", "unit": "count", "description": "Switched outlet table size. AP8853 returns 0, so per-outlet switching is not supported.", "oid": "1.3.6.1.4.1.318.1.1.26.9.1.0", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "pdu_outlet_metered_table_size", "name": "PDU Outlet Metered Table Size", "category": "Power", "data_type": "integer", "unit": "count", "description": "Metered outlet table size. AP8853 returns 0, so per-outlet metering is not supported.", "oid": "1.3.6.1.4.1.318.1.1.26.9.3.0", "map_type": "integer", "scale": "1", "offset": "0"},

    # Group summary
    {"code": "pdu_group_total_power_kw", "name": "PDU Group Total Power", "category": "Power", "data_type": "float", "unit": "kW", "description": "Rack PDU group total power. Raw value is divided by 100.", "oid": "1.3.6.1.4.1.318.1.1.26.11.2.0", "map_type": "integer", "scale": "0.01", "offset": "0"},
    {"code": "pdu_group_total_energy", "name": "PDU Group Total Energy", "category": "Energy", "data_type": "float", "unit": "kWh", "description": "Rack PDU group total energy counter.", "oid": "1.3.6.1.4.1.318.1.1.26.11.3.0", "map_type": "integer", "scale": "1", "offset": "0"},

    # Generic SNMP/network health. These are generic MIB-II OIDs, not PowerNet rPDU2 branch.
    {"code": "device_uptime", "name": "Device Uptime", "category": "Status", "data_type": "float", "unit": "seconds", "description": "Device uptime from sysUpTime. Raw TimeTicks are divided by 100 to seconds.", "oid": "1.3.6.1.2.1.1.3.0", "map_type": "timeticks", "scale": "0.01", "offset": "0"},
    {"code": "device_interface_oper_status", "name": "Device Interface Operational Status", "category": "Network", "data_type": "integer", "unit": "state", "description": "Interface operational status for interface index 2.", "oid": "1.3.6.1.2.1.2.2.1.8.2", "map_type": "integer", "scale": "1", "offset": "0"},
    {"code": "device_interface_speed", "name": "Device Interface Speed", "category": "Network", "data_type": "float", "unit": "bps", "description": "Interface speed in bits per second for interface index 2.", "oid": "1.3.6.1.2.1.2.2.1.5.2", "map_type": "gauge", "scale": "1", "offset": "0"},
    {"code": "snmp_bad_community_uses", "name": "SNMP Bad Community Uses", "category": "Security", "data_type": "integer", "unit": "count", "description": "SNMP bad community counter.", "oid": "1.3.6.1.2.1.11.6.0", "map_type": "counter", "scale": "1", "offset": "0"},
]


def metric_defaults(row):
    defaults = {
        "name": row["name"],
        "category": row["category"],
        "data_type": row["data_type"],
        "unit": row["unit"],
        "description": row["description"],
        "is_active": True,
    }
    return {k: v for k, v in defaults.items() if any(f.name == k for f in MetricDefinition._meta.fields)}


def mapping_defaults(row, vendor, device_type, device_model, metric):
    defaults = {
        "vendor": vendor,
        "device_type": device_type,
        "device_model": device_model,
        "metric": metric,
        "oid": row["oid"],
        "data_type": row["map_type"],
        "scale_factor": Decimal(row["scale"]),
        "offset_value": Decimal(row["offset"]),
        "is_active": True,
    }
    return {k: v for k, v in defaults.items() if any(f.name == k for f in SNMPOIDMapping._meta.fields)}


@transaction.atomic
def main():
    vendor = get_or_create_vendor()
    device_type = get_or_create_device_type()
    device_model = get_or_create_device_model(vendor, device_type)

    metric_created = 0
    metric_updated = 0
    mapping_created = 0
    mapping_updated = 0

    for row in METRICS:
        metric, created = MetricDefinition.objects.update_or_create(
            code=row["code"],
            defaults=metric_defaults(row),
        )
        metric_created += int(created)
        metric_updated += int(not created)

        # Prefer uniqueness by device model + metric. If your model has a different
        # unique constraint, update_or_create with oid still keeps the script idempotent.
        lookup = {"device_model": device_model, "metric": metric}
        if not all(any(f.name == k for f in SNMPOIDMapping._meta.fields) for k in lookup):
            lookup = {"metric": metric, "oid": row["oid"]}

        mapping, created = SNMPOIDMapping.objects.update_or_create(
            **lookup,
            defaults=mapping_defaults(row, vendor, device_type, device_model, metric),
        )
        mapping_created += int(created)
        mapping_updated += int(not created)

    print("APC AP8853 Rack PDU seed completed")
    print(f"Vendor: {vendor}")
    print(f"Device type: {device_type}")
    print(f"Device model: {device_model}")
    print(f"Metric definitions created: {metric_created}, updated: {metric_updated}")
    print(f"SNMP OID mappings created: {mapping_created}, updated: {mapping_updated}")
    print(f"Total rows processed: {len(METRICS)}")


main()

