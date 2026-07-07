from decimal import Decimal
from django.apps import apps
from django.db import transaction

Vendor = apps.get_model("devices", "Vendor")
DeviceType = apps.get_model("devices", "DeviceType")
DeviceModel = apps.get_model("devices", "DeviceModel")
MetricDefinition = apps.get_model("telemetry", "MetricDefinition")
SNMPOIDMapping = apps.get_model("devices", "SNMPOIDMapping")


VENDOR_NAME = "Schneider Electric / APC"
DEVICE_TYPE_NAME = "NetBotz"
DEVICE_MODEL_NAME = "APC NetBotz 450"


def get_required_objects():
    vendor = Vendor.objects.get(name=VENDOR_NAME)
    device_type = DeviceType.objects.get(name=DEVICE_TYPE_NAME)
    device_model = DeviceModel.objects.get(
        vendor=vendor,
        device_type=device_type,
        name=DEVICE_MODEL_NAME,
    )
    return vendor, device_type, device_model


METRICS = [
    # Environmental / top cards
    {
        "code": "netbotz_temperature",
        "name": "NetBotz Temperature",
        "category": "Environment",
        "data_type": "float",
        "unit": "°C",
        "description": "NetBotz room temperature reading in Celsius.",
    },
    {
        "code": "netbotz_humidity",
        "name": "NetBotz Humidity",
        "category": "Environment",
        "data_type": "float",
        "unit": "%RH",
        "description": "NetBotz room relative humidity percentage.",
    },
    {
        "code": "netbotz_dew_point",
        "name": "NetBotz Dew Point",
        "category": "Environment",
        "data_type": "float",
        "unit": "°C",
        "description": "NetBotz dew point temperature in Celsius.",
    },

    # Digital / safety sensors
    {
        "code": "netbotz_water_leak",
        "name": "NetBotz Water Leak",
        "category": "Safety",
        "data_type": "string",
        "unit": "",
        "description": "Water leak rope status. Normal value is No Leak.",
    },
    {
        "code": "netbotz_beacon",
        "name": "NetBotz Beacon",
        "category": "Safety",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz beacon status. Normal value is Off.",
    },
    {
        "code": "netbotz_power_room_smoke",
        "name": "NetBotz Power Room Smoke",
        "category": "Safety",
        "data_type": "string",
        "unit": "",
        "description": "Power Room smoke detector status. Normal value is No Smoke.",
    },
    {
        "code": "netbotz_network_room_smoke",
        "name": "NetBotz Network Room Smoke",
        "category": "Safety",
        "data_type": "string",
        "unit": "",
        "description": "Network Room smoke detector status. Normal value is No Smoke.",
    },
    {
        "code": "netbotz_ethernet_link",
        "name": "NetBotz Ethernet Link",
        "category": "Network",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz Ethernet link status from digital sensor table. Normal value is Up.",
    },
    {
        "code": "netbotz_alink_bus_power",
        "name": "NetBotz A-Link Bus Power",
        "category": "Power",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz A-Link bus power status. Normal value is OK.",
    },

    # Connectivity / ports
    {
        "code": "netbotz_alink_sensor_count",
        "name": "NetBotz A-Link Sensor Count",
        "category": "Connectivity",
        "data_type": "integer",
        "unit": "",
        "description": "Number of A-Link sensors connected to the NetBotz appliance.",
    },
    {
        "code": "netbotz_main_enclosure",
        "name": "NetBotz Main Enclosure",
        "category": "Device",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz main enclosure name.",
    },
    {
        "code": "netbotz_sensor_pod",
        "name": "NetBotz Sensor Pod",
        "category": "Device",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz connected sensor pod name.",
    },
    {
        "code": "netbotz_4_20ma_port_71",
        "name": "NetBotz 4-20mA Port 71",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz 4-20mA input port 71.",
    },
    {
        "code": "netbotz_4_20ma_port_72",
        "name": "NetBotz 4-20mA Port 72",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz 4-20mA input port 72.",
    },
    {
        "code": "netbotz_4_20ma_port_73",
        "name": "NetBotz 4-20mA Port 73",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz 4-20mA input port 73.",
    },
    {
        "code": "netbotz_4_20ma_port_74",
        "name": "NetBotz 4-20mA Port 74",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz 4-20mA input port 74.",
    },
    {
        "code": "netbotz_usb_port_a",
        "name": "NetBotz USB Port A",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz USB Port A label/status.",
    },
    {
        "code": "netbotz_usb_port_b",
        "name": "NetBotz USB Port B",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz USB Port B label/status.",
    },
    {
        "code": "netbotz_usb_port_c",
        "name": "NetBotz USB Port C",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz USB Port C label/status.",
    },
    {
        "code": "netbotz_usb_port_d",
        "name": "NetBotz USB Port D",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz USB Port D label/status.",
    },
    {
        "code": "netbotz_rs485_port",
        "name": "NetBotz RS-485 Port",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz RS-485 port label/status.",
    },
    {
        "code": "netbotz_relay_output_1",
        "name": "NetBotz Relay Output 1",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz relay output 1 label/status.",
    },
    {
        "code": "netbotz_relay_output_2",
        "name": "NetBotz Relay Output 2",
        "category": "Connectivity",
        "data_type": "string",
        "unit": "",
        "description": "NetBotz relay output 2 label/status.",
    },

    # Device information
    {
        "code": "netbotz_system_description",
        "name": "NetBotz System Description",
        "category": "Device",
        "data_type": "string",
        "unit": "",
        "description": "SNMP system description of the NetBotz appliance.",
    },
    {
        "code": "netbotz_system_object_id",
        "name": "NetBotz System Object ID",
        "category": "Device",
        "data_type": "string",
        "unit": "",
        "description": "SNMP system object identifier used to identify the NetBotz model.",
    },
    {
        "code": "netbotz_device_uptime",
        "name": "NetBotz Device Uptime",
        "category": "Device",
        "data_type": "timeticks",
        "unit": "ticks",
        "description": "SNMP sysUpTime value for the NetBotz appliance.",
    },
    {
        "code": "netbotz_system_name",
        "name": "NetBotz System Name",
        "category": "Device",
        "data_type": "string",
        "unit": "",
        "description": "SNMP system name of the NetBotz appliance.",
    },
    {
        "code": "netbotz_interface_status",
        "name": "NetBotz Interface Status",
        "category": "Network",
        "data_type": "integer",
        "unit": "",
        "description": "Primary Ethernet interface operational status. Normal value is up(1).",
    },
    {
        "code": "netbotz_interface_speed",
        "name": "NetBotz Interface Speed",
        "category": "Network",
        "data_type": "integer",
        "unit": "Mbps",
        "description": "Primary Ethernet interface speed in Mbps.",
    },
    {
        "code": "netbotz_mac_address",
        "name": "NetBotz MAC Address",
        "category": "Network",
        "data_type": "string",
        "unit": "",
        "description": "Primary Ethernet interface MAC address.",
    },

    # SNMP health counters
    {
        "code": "netbotz_snmp_in_packets",
        "name": "NetBotz SNMP In Packets",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "packets",
        "description": "Total SNMP packets received by the NetBotz appliance.",
    },
    {
        "code": "netbotz_snmp_out_packets",
        "name": "NetBotz SNMP Out Packets",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "packets",
        "description": "Total SNMP packets sent by the NetBotz appliance.",
    },
    {
        "code": "netbotz_snmp_bad_versions",
        "name": "NetBotz SNMP Bad Versions",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP packets received with bad SNMP version.",
    },
    {
        "code": "netbotz_snmp_bad_community_names",
        "name": "NetBotz SNMP Bad Community Names",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP packets received with bad community names.",
    },
    {
        "code": "netbotz_snmp_bad_community_uses",
        "name": "NetBotz SNMP Bad Community Uses",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP packets received with invalid community usage.",
    },
    {
        "code": "netbotz_snmp_asn_parse_errors",
        "name": "NetBotz SNMP ASN Parse Errors",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP ASN parse error counter.",
    },
    {
        "code": "netbotz_snmp_no_such_names",
        "name": "NetBotz SNMP No Such Names",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP noSuchName error counter.",
    },
    {
        "code": "netbotz_snmp_bad_values",
        "name": "NetBotz SNMP Bad Values",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP bad value error counter.",
    },
    {
        "code": "netbotz_snmp_general_errors",
        "name": "NetBotz SNMP General Errors",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP general error counter.",
    },
    {
        "code": "netbotz_snmp_silent_drops",
        "name": "NetBotz SNMP Silent Drops",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP silent drop counter.",
    },
    {
        "code": "netbotz_snmp_proxy_drops",
        "name": "NetBotz SNMP Proxy Drops",
        "category": "SNMP",
        "data_type": "integer",
        "unit": "count",
        "description": "SNMP proxy drop counter.",
    },
]


OID_MAPPINGS = [
    # Environmental / top cards
    ("netbotz_temperature", "1.3.6.1.4.1.5528.100.4.1.10.1.7.1619732064", "string", "1.000000", "0.000000"),
    ("netbotz_humidity", "1.3.6.1.4.1.5528.100.4.1.10.1.7.1974269701", "string", "1.000000", "0.000000"),
    ("netbotz_dew_point", "1.3.6.1.4.1.5528.100.4.1.10.1.7.2231143474", "string", "1.000000", "0.000000"),

    # Digital / safety sensor values
    ("netbotz_water_leak", "1.3.6.1.4.1.5528.100.4.2.10.1.7.399845582", "string", "1.000000", "0.000000"),
    ("netbotz_beacon", "1.3.6.1.4.1.5528.100.4.2.10.1.7.1117668031", "string", "1.000000", "0.000000"),
    ("netbotz_power_room_smoke", "1.3.6.1.4.1.5528.100.4.2.10.1.7.1649493300", "string", "1.000000", "0.000000"),
    ("netbotz_network_room_smoke", "1.3.6.1.4.1.5528.100.4.2.10.1.7.4201958908", "string", "1.000000", "0.000000"),
    ("netbotz_ethernet_link", "1.3.6.1.4.1.5528.100.4.2.10.1.7.3502248167", "string", "1.000000", "0.000000"),
    ("netbotz_alink_bus_power", "1.3.6.1.4.1.5528.100.4.2.10.1.7.3823829717", "string", "1.000000", "0.000000"),

    # Connectivity / ports
    ("netbotz_alink_sensor_count", "1.3.6.1.4.1.5528.100.2.2.1.1.2.2138452156", "integer", "1.000000", "0.000000"),
    ("netbotz_main_enclosure", "1.3.6.1.4.1.5528.100.2.1.1.4.2138452156", "string", "1.000000", "0.000000"),
    ("netbotz_sensor_pod", "1.3.6.1.4.1.5528.100.2.1.1.4.3830159408", "string", "1.000000", "0.000000"),
    ("netbotz_4_20ma_port_71", "1.3.6.1.4.1.5528.100.3.1.1.5.515365348", "string", "1.000000", "0.000000"),
    ("netbotz_4_20ma_port_72", "1.3.6.1.4.1.5528.100.3.1.1.5.1815442662", "string", "1.000000", "0.000000"),
    ("netbotz_4_20ma_port_73", "1.3.6.1.4.1.5528.100.3.1.1.5.1097539410", "string", "1.000000", "0.000000"),
    ("netbotz_4_20ma_port_74", "1.3.6.1.4.1.5528.100.3.1.1.5.2275027887", "string", "1.000000", "0.000000"),
    ("netbotz_usb_port_a", "1.3.6.1.4.1.5528.100.3.10.1.3.16952931", "string", "1.000000", "0.000000"),
    ("netbotz_usb_port_b", "1.3.6.1.4.1.5528.100.3.10.1.3.3984426841", "string", "1.000000", "0.000000"),
    ("netbotz_usb_port_c", "1.3.6.1.4.1.5528.100.3.10.1.3.391043835", "string", "1.000000", "0.000000"),
    ("netbotz_usb_port_d", "1.3.6.1.4.1.5528.100.3.10.1.3.1173338172", "string", "1.000000", "0.000000"),
    ("netbotz_rs485_port", "1.3.6.1.4.1.5528.100.3.10.1.3.1616024485", "string", "1.000000", "0.000000"),
    ("netbotz_relay_output_1", "1.3.6.1.4.1.5528.100.3.10.1.3.1984299590", "string", "1.000000", "0.000000"),
    ("netbotz_relay_output_2", "1.3.6.1.4.1.5528.100.3.10.1.3.2282202569", "string", "1.000000", "0.000000"),

    # Device information
    ("netbotz_system_description", "1.3.6.1.2.1.1.1.0", "string", "1.000000", "0.000000"),
    ("netbotz_system_object_id", "1.3.6.1.2.1.1.2.0", "string", "1.000000", "0.000000"),
    ("netbotz_device_uptime", "1.3.6.1.2.1.1.3.0", "timeticks", "1.000000", "0.000000"),
    ("netbotz_system_name", "1.3.6.1.2.1.1.5.0", "string", "1.000000", "0.000000"),
    ("netbotz_interface_status", "1.3.6.1.2.1.2.2.1.8.3", "integer", "1.000000", "0.000000"),
    ("netbotz_interface_speed", "1.3.6.1.2.1.31.1.1.1.15.3", "integer", "1.000000", "0.000000"),
    ("netbotz_mac_address", "1.3.6.1.2.1.2.2.1.6.3", "string", "1.000000", "0.000000"),

    # SNMP health counters
    ("netbotz_snmp_in_packets", "1.3.6.1.2.1.11.1.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_out_packets", "1.3.6.1.2.1.11.2.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_bad_versions", "1.3.6.1.2.1.11.3.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_bad_community_names", "1.3.6.1.2.1.11.4.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_bad_community_uses", "1.3.6.1.2.1.11.5.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_asn_parse_errors", "1.3.6.1.2.1.11.6.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_no_such_names", "1.3.6.1.2.1.11.9.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_bad_values", "1.3.6.1.2.1.11.10.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_general_errors", "1.3.6.1.2.1.11.12.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_silent_drops", "1.3.6.1.2.1.11.31.0", "integer", "1.000000", "0.000000"),
    ("netbotz_snmp_proxy_drops", "1.3.6.1.2.1.11.32.0", "integer", "1.000000", "0.000000"),
]


@transaction.atomic
def run():
    vendor, device_type, device_model = get_required_objects()

    created_metrics = 0
    updated_metrics = 0
    created_mappings = 0
    updated_mappings = 0

    metric_by_code = {}

    for item in METRICS:
        metric, created = MetricDefinition.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "category": item["category"],
                "data_type": item["data_type"],
                "unit": item["unit"],
                "description": item["description"],
                "is_active": True,
            },
        )
        metric_by_code[item["code"]] = metric

        if created:
            created_metrics += 1
        else:
            updated_metrics += 1

    for metric_code, oid, data_type, scale_factor, offset_value in OID_MAPPINGS:
        metric = metric_by_code[metric_code]

        mapping, created = SNMPOIDMapping.objects.update_or_create(
            device_type=device_type,
            vendor=vendor,
            device_model=device_model,
            metric=metric,
            oid=oid,
            defaults={
                "data_type": data_type,
                "scale_factor": Decimal(scale_factor),
                "offset_value": Decimal(offset_value),
                "is_active": True,
            },
        )

        if created:
            created_mappings += 1
        else:
            updated_mappings += 1

    print("NetBotz 450 metric/OID seed completed.")
    print(f"Metrics created: {created_metrics}")
    print(f"Metrics updated: {updated_metrics}")
    print(f"OID mappings created: {created_mappings}")
    print(f"OID mappings updated: {updated_mappings}")


run()
