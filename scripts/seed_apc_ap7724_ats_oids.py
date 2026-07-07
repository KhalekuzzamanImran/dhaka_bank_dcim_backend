from decimal import Decimal
from django.apps import apps
from django.db import transaction

Vendor = apps.get_model("devices", "Vendor")
DeviceType = apps.get_model("devices", "DeviceType")
DeviceModel = apps.get_model("devices", "DeviceModel")
SNMPOIDMapping = apps.get_model("devices", "SNMPOIDMapping")
MetricDefinition = apps.get_model("telemetry", "MetricDefinition")

VENDOR_NAME = "Schneider Electric / APC"
DEVICE_TYPE_NAME = "ATS"
DEVICE_MODEL_NUMBER = "AP7724"


def get_choice_value(model, field_name, wanted):
    field = model._meta.get_field(field_name)
    if not field.choices:
        return wanted

    wanted_norm = str(wanted).strip().lower()

    for value, label in field.choices:
        if str(value).strip().lower() == wanted_norm:
            return value
        if str(label).strip().lower() == wanted_norm:
            return value

    return wanted


def model_has_field(model, field_name):
    return field_name in [field.name for field in model._meta.fields]


def create_metric(row):
    data_type = get_choice_value(
        MetricDefinition,
        "data_type",
        row["metric_data_type"],
    )

    defaults = {
        "name": row["name"],
        "data_type": data_type,
        "unit": row.get("unit", ""),
        "description": row.get("description", ""),
        "is_active": True,
    }

    if model_has_field(MetricDefinition, "category"):
        defaults["category"] = row.get("category", "Other")

    metric, created = MetricDefinition.objects.update_or_create(
        code=row["code"],
        defaults=defaults,
    )
    return metric, created


def create_oid_mapping(vendor, device_type, device_model, metric, row):
    data_type = get_choice_value(
        SNMPOIDMapping,
        "data_type",
        row["snmp_data_type"],
    )

    defaults = {
        "vendor": vendor,
        "device_type": device_type,
        "device_model": device_model,
        "metric": metric,
        "data_type": data_type,
        "scale_factor": Decimal(str(row.get("scale_factor", "1.0"))),
        "offset_value": Decimal(str(row.get("offset_value", "0.0"))),
        "is_active": True,
    }

    mapping, created = SNMPOIDMapping.objects.update_or_create(
        vendor=vendor,
        device_type=device_type,
        device_model=device_model,
        metric=metric,
        oid=row["oid"],
        defaults=defaults,
    )
    return mapping, created


metric_rows = [
    # Identity / inventory
    {
        "code": "ats_hardware_revision",
        "name": "ATS Hardware Revision",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS hardware revision.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.1.0",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_firmware_revision",
        "name": "ATS Firmware Revision",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS firmware revision.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.2.0",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_firmware_date",
        "name": "ATS Firmware Date",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS firmware release date.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.3.0",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_manufacture_date",
        "name": "ATS Manufacture Date",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS date of manufacture.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.4.0",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_model_number",
        "name": "ATS Model Number",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS model number.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.5.0",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_serial_number",
        "name": "ATS Serial Number",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS serial number.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.6.0",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_nominal_line_voltage",
        "name": "ATS Nominal Line Voltage",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "V",
        "description": "ATS nominal line voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.7.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_nominal_line_frequency",
        "name": "ATS Nominal Line Frequency",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "Hz",
        "description": "ATS nominal line frequency.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.8.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_device_rating",
        "name": "ATS Device Rating",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "ATS rated current capacity.",
        "oid": "1.3.6.1.4.1.318.1.1.8.1.9.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_product_name",
        "name": "ATS Product Name",
        "category": "Inventory",
        "metric_data_type": "text",
        "unit": "",
        "description": "Configured ATS product name.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.1.0",
        "snmp_data_type": "string",
    },

    # Configuration / thresholds
    {
        "code": "ats_preferred_source",
        "name": "ATS Preferred Source",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "Configured preferred input source.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.2.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_voltage_sensitivity",
        "name": "ATS Voltage Sensitivity",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS voltage sensitivity setting.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.4.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_transfer_voltage_range",
        "name": "ATS Transfer Voltage Range",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS transfer voltage range setting.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.5.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_current_limit",
        "name": "ATS Current Limit",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Configured ATS current limit.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.6.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_total_near_overload_threshold",
        "name": "ATS Total Near Overload Threshold",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Total output near-overload threshold.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.14.1.4.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank1_near_overload_threshold",
        "name": "ATS Bank 1 Near Overload Threshold",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Bank 1 near-overload threshold.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.14.1.4.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank2_near_overload_threshold",
        "name": "ATS Bank 2 Near Overload Threshold",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Bank 2 near-overload threshold.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.14.1.4.3",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_total_overload_threshold",
        "name": "ATS Total Overload Threshold",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Total output overload threshold.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.14.1.5.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank1_overload_threshold",
        "name": "ATS Bank 1 Overload Threshold",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Bank 1 overload threshold.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.14.1.5.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank2_overload_threshold",
        "name": "ATS Bank 2 Overload Threshold",
        "category": "Threshold",
        "metric_data_type": "integer",
        "unit": "A",
        "description": "Bank 2 overload threshold.",
        "oid": "1.3.6.1.4.1.318.1.1.8.4.14.1.5.3",
        "snmp_data_type": "integer",
    },

    # Main status
    {
        "code": "ats_communication_status",
        "name": "ATS Communication Status",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS communication status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.1.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_selected_source",
        "name": "ATS Selected Source",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "Currently selected input source.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.2.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_redundancy_state",
        "name": "ATS Redundancy State",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS redundancy state.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.3.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_over_current_state",
        "name": "ATS Over Current State",
        "category": "Alarm",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS over-current state.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.4.0",
        "snmp_data_type": "integer",
    },

    # PSU / hardware states
    {
        "code": "ats_5v_power_supply_status",
        "name": "ATS 5V Power Supply Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS 5V internal power supply status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.5.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_24v_power_supply_status",
        "name": "ATS 24V Power Supply Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS 24V internal power supply status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.6.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_24v_source_b_power_supply_status",
        "name": "ATS 24V Source B Power Supply Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS 24V Source B internal power supply status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.7.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_plus_12v_power_supply_status",
        "name": "ATS Plus 12V Power Supply Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS +12V internal power supply status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.8.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_minus_12v_power_supply_status",
        "name": "ATS Minus 12V Power Supply Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS -12V internal power supply status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.9.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_switch_status",
        "name": "ATS Switch Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS switch status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.10.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_front_panel_status",
        "name": "ATS Front Panel Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS front panel status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.11.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_a_status",
        "name": "ATS Source A Status",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS Source A status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.12.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_b_status",
        "name": "ATS Source B Status",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS Source B status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.13.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_phase_sync_status",
        "name": "ATS Phase Sync Status",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS phase synchronization status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.14.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_output_voltage_status",
        "name": "ATS Output Voltage Status",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS output voltage status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.15.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_hardware_status",
        "name": "ATS Hardware Status",
        "category": "Hardware",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS hardware status.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.1.16.0",
        "snmp_data_type": "integer",
    },

    # Input source measurements
    {
        "code": "ats_source_a_frequency",
        "name": "ATS Source A Frequency",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "Hz",
        "description": "ATS Source A input frequency.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.2.1.4.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_b_frequency",
        "name": "ATS Source B Frequency",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "Hz",
        "description": "ATS Source B input frequency.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.2.1.4.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_a_type",
        "name": "ATS Source A Type",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS Source A input type.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.2.1.5.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_b_type",
        "name": "ATS Source B Type",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS Source B input type.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.2.1.5.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_a_name",
        "name": "ATS Source A Name",
        "category": "Configuration",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS Source A configured name.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.2.1.6.1",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_source_b_name",
        "name": "ATS Source B Name",
        "category": "Configuration",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS Source B configured name.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.2.1.6.2",
        "snmp_data_type": "string",
    },
    {
        "code": "ats_source_a_voltage",
        "name": "ATS Source A Voltage",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "V",
        "description": "ATS Source A input voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.3.1.3.1.1.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_source_b_voltage",
        "name": "ATS Source B Voltage",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "V",
        "description": "ATS Source B input voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.3.3.1.3.2.1.1",
        "snmp_data_type": "integer",
    },

    # Output measurements
    {
        "code": "ats_output_voltage_orientation",
        "name": "ATS Output Voltage Orientation",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS output voltage orientation.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.2.1.3.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_output_frequency",
        "name": "ATS Output Frequency",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "Hz",
        "description": "ATS output frequency.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.2.1.4.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_output_bank_total_type",
        "name": "ATS Output Bank Total Type",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS output bank type for total bank.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.3.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_output_bank1_type",
        "name": "ATS Output Bank 1 Type",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS output bank type for bank 1.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.3.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_output_bank2_type",
        "name": "ATS Output Bank 2 Type",
        "category": "Configuration",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS output bank type for bank 2.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.3.3",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_total_output_current",
        "name": "ATS Total Output Current",
        "category": "Power",
        "metric_data_type": "float",
        "unit": "A",
        "description": "ATS total output current.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.4.1",
        "snmp_data_type": "gauge32",
        "scale_factor": "1.0",
    },
    {
        "code": "ats_bank1_output_current",
        "name": "ATS Bank 1 Output Current",
        "category": "Power",
        "metric_data_type": "float",
        "unit": "A",
        "description": "ATS bank 1 output current.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.4.2",
        "snmp_data_type": "gauge32",
        "scale_factor": "1.0",
    },
    {
        "code": "ats_bank2_output_current",
        "name": "ATS Bank 2 Output Current",
        "category": "Power",
        "metric_data_type": "float",
        "unit": "A",
        "description": "ATS bank 2 output current.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.4.3",
        "snmp_data_type": "gauge32",
        "scale_factor": "1.0",
    },
    {
        "code": "ats_total_output_bank_state",
        "name": "ATS Total Output Bank State",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS total output bank load state.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.5.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank1_output_bank_state",
        "name": "ATS Bank 1 Output Bank State",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS bank 1 load state.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.5.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank2_output_bank_state",
        "name": "ATS Bank 2 Output Bank State",
        "category": "Status",
        "metric_data_type": "integer",
        "unit": "",
        "description": "ATS bank 2 load state.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.5.3",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_total_output_voltage",
        "name": "ATS Total Output Voltage",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "V",
        "description": "ATS total output voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.6.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank1_output_voltage",
        "name": "ATS Bank 1 Output Voltage",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "V",
        "description": "ATS bank 1 output voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.6.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank2_output_voltage",
        "name": "ATS Bank 2 Output Voltage",
        "category": "Power",
        "metric_data_type": "integer",
        "unit": "V",
        "description": "ATS bank 2 output voltage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.6.3",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_total_load_percent",
        "name": "ATS Total Load Percent",
        "category": "Power",
        "metric_data_type": "float",
        "unit": "%",
        "description": "ATS total output load percentage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.12.1",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank1_load_percent",
        "name": "ATS Bank 1 Load Percent",
        "category": "Power",
        "metric_data_type": "float",
        "unit": "%",
        "description": "ATS bank 1 output load percentage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.12.2",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_bank2_load_percent",
        "name": "ATS Bank 2 Load Percent",
        "category": "Power",
        "metric_data_type": "float",
        "unit": "%",
        "description": "ATS bank 2 output load percentage.",
        "oid": "1.3.6.1.4.1.318.1.1.8.5.4.5.1.12.3",
        "snmp_data_type": "integer",
    },

    # Trap receiver configuration
    {
        "code": "ats_trap_receiver_count",
        "name": "ATS Trap Receiver Count",
        "category": "Network",
        "metric_data_type": "integer",
        "unit": "",
        "description": "Number of configured trap receiver slots.",
        "oid": "1.3.6.1.4.1.318.2.1.1.0",
        "snmp_data_type": "integer",
    },
    {
        "code": "ats_trap_receiver_1_address",
        "name": "ATS Trap Receiver 1 Address",
        "category": "Network",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS trap receiver 1 IP address.",
        "oid": "1.3.6.1.4.1.318.2.1.2.1.2.1",
        "snmp_data_type": "ipaddress",
    },
    {
        "code": "ats_trap_receiver_2_address",
        "name": "ATS Trap Receiver 2 Address",
        "category": "Network",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS trap receiver 2 IP address.",
        "oid": "1.3.6.1.4.1.318.2.1.2.1.2.2",
        "snmp_data_type": "ipaddress",
    },
    {
        "code": "ats_trap_receiver_3_address",
        "name": "ATS Trap Receiver 3 Address",
        "category": "Network",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS trap receiver 3 IP address.",
        "oid": "1.3.6.1.4.1.318.2.1.2.1.2.3",
        "snmp_data_type": "ipaddress",
    },
    {
        "code": "ats_trap_receiver_4_address",
        "name": "ATS Trap Receiver 4 Address",
        "category": "Network",
        "metric_data_type": "text",
        "unit": "",
        "description": "ATS trap receiver 4 IP address.",
        "oid": "1.3.6.1.4.1.318.2.1.2.1.2.4",
        "snmp_data_type": "ipaddress",
    },
]


with transaction.atomic():
    vendor = Vendor.objects.get(name=VENDOR_NAME)
    device_type = DeviceType.objects.get(name=DEVICE_TYPE_NAME)
    device_model = DeviceModel.objects.get(
        vendor=vendor,
        device_type=device_type,
        model_number=DEVICE_MODEL_NUMBER,
    )

    created_metrics = 0
    updated_metrics = 0
    created_mappings = 0
    updated_mappings = 0

    for row in metric_rows:
        metric, metric_created = create_metric(row)

        _, mapping_created = create_oid_mapping(
            vendor=vendor,
            device_type=device_type,
            device_model=device_model,
            metric=metric,
            row=row,
        )

        if metric_created:
            created_metrics += 1
        else:
            updated_metrics += 1

        if mapping_created:
            created_mappings += 1
        else:
            updated_mappings += 1

print("APC AP7724 ATS OID seed completed.")
print(f"Metrics created: {created_metrics}")
print(f"Metrics updated: {updated_metrics}")
print(f"OID mappings created: {created_mappings}")
print(f"OID mappings updated: {updated_mappings}")
print(f"Vendor: {vendor}")
print(f"Device type: {device_type}")
print(f"Device model: {device_model}")
