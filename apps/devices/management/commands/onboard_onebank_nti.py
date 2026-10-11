from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.datacenters.models import DataCenter
from apps.devices.models import (
    Device,
    DeviceCredential,
    DeviceModel,
    DevicePollingConfig,
    DeviceProtocolConfig,
    DeviceStatus,
    DeviceType,
    PollingPriority,
    PollingProfile,
    ProtocolType,
    SNMPVersion,
    SNMPOIDMapping,
    Vendor,
)
from apps.organizations.models import Organization
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition
from apps.traps.models import SNMPTrapOIDMapping, SNMPTrapSource, TrapSeverity
from collectors.snmp_collector.security import encrypt_secret


DEVICE_IP = "10.156.0.155"
DEVICE_TYPE_CODE = "NETBOTZ"
VENDOR_CODE = "NTI"
MODEL_NUMBER = "ENVIROMUX-16D"
PRODUCT_OID = "1.3.6.1.4.1.3699.1.1.11"
PRIVATE_OID_ROOT = f"{PRODUCT_OID}.1"
TRAP_OID_ROOT = f"{PRODUCT_OID}.100"

# Names and inputs were read from 10.156.0.155 using the supplied SNMPv2c
# community. The ENVIROMUX-2D MIB in this repository documents the table
# layouts and trap suffixes; the agent's sysObjectID identifies product .11.
DIGITAL_INPUTS = (
    (1, "DC Smoke", 1),
    (2, "DC Smoke Sensor -2", 1),
    (3, "Power Room Smoke", 1),
    (4, "DC Door-1", 0),
    (5, "DC Door-2", 0),
    (6, "Power Failure / Power On", 0),
    (7, "Power Room Door-1", 0),
    (8, "Motion sensor DC Main Gate", 0),
)


def _metric(code, name, category, data_type, unit, description):
    return {
        "code": code,
        "name": name,
        "category": category,
        "data_type": data_type,
        "unit": unit,
        "description": description,
    }


METRICS = (
    _metric(
        "netbotz_temperature", "NetBotz Temperature", MetricCategory.ENVIRONMENT,
        MetricDataType.FLOAT, "°C", "NTI ENVIROMUX-16D internal temperature in Celsius.",
    ),
    _metric(
        "netbotz_humidity", "NetBotz Humidity", MetricCategory.ENVIRONMENT,
        MetricDataType.FLOAT, "%RH", "NTI ENVIROMUX-16D internal relative humidity.",
    ),
    _metric(
        "netbotz_system_name", "NetBotz System Name", MetricCategory.STATUS,
        MetricDataType.TEXT, "", "SNMP system name reported by the NTI appliance.",
    ),
    _metric(
        "netbotz_hardware_revision", "NetBotz Firmware Version", MetricCategory.STATUS,
        MetricDataType.TEXT, "", "NTI ENVIROMUX firmware version.",
    ),
    _metric(
        "netbotz_device_uptime", "NetBotz Device Uptime", MetricCategory.STATUS,
        MetricDataType.INTEGER, "ticks", "SNMP sysUpTime for the NTI appliance.",
    ),
    *(
        _metric(
            f"netbotz_nti_digital_input_{index}_{kind}",
            f"NTI Digital Input {index} {kind.title()}",
            MetricCategory.STATUS,
            MetricDataType.INTEGER,
            "state",
            f"NTI ENVIROMUX-16D input {index} {description}: raw {kind} enum from the NTI MIB.",
        )
        for index, description, _normal_value in DIGITAL_INPUTS
        for kind in ("value", "status")
    ),
)


def _mapping_rows():
    rows = [
        ("netbotz_temperature", f"{PRIVATE_OID_ROOT}.3.1.1.6.1", "integer", "0.1"),
        ("netbotz_humidity", f"{PRIVATE_OID_ROOT}.3.1.1.6.2", "integer", "1"),
        ("netbotz_system_name", "1.3.6.1.2.1.1.5.0", "string", "1"),
        ("netbotz_hardware_revision", f"{PRODUCT_OID}.1.1.8.0", "string", "1"),
        ("netbotz_device_uptime", "1.3.6.1.2.1.1.3.0", "timeticks", "1"),
    ]
    rows.extend(
        (f"netbotz_nti_digital_input_{index}_{kind}", f"{PRIVATE_OID_ROOT}.6.1.1.{column}.{index}", "integer", "1")
        for index, _description, _normal_value in DIGITAL_INPUTS
        for kind, column in (("value", 7), ("status", 8))
    )
    return tuple(rows)


OID_MAPPINGS = _mapping_rows()

# The ENVIROMUX MIB defines one internal sensor trap and five digital-input
# traps. The MIB's product branch is 2D (.9); this live 16D reports .11.
TRAP_MAPPINGS = (
    {
        "trap_oid": f"{TRAP_OID_ROOT}.3.0.1",
        "event_code": "nti_internal_sensor_changed",
        "event_name": "NTI Internal Sensor Alert",
        "message_template": "NTI internal environmental sensor reported an event.",
        "severity": TrapSeverity.WARNING,
    },
    *(
        {
            "trap_oid": f"{TRAP_OID_ROOT}.6.0.{index}",
            "event_code": f"nti_digital_input_{index}_changed",
            "event_name": f"NTI {description} Alert",
            "message_template": f"NTI digital input {index} ({description}) reported an event.",
            "severity": TrapSeverity.WARNING,
        }
        for index, description, _normal_value in DIGITAL_INPUTS[:5]
    ),
)


class Command(BaseCommand):
    help = "Onboard the One Bank NTI ENVIROMUX-16D and its frontend-required SNMP telemetry."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def _resolve_location(self, device_type):
        reference = (
            Device.objects.select_related("organization", "data_center", "room", "rack")
            .filter(device_type=device_type, is_active=True)
            .exclude(ip_address=DEVICE_IP)
            .first()
        )
        if reference:
            return reference.organization, reference.data_center, reference.room, reference.rack

        organizations = list(Organization.objects.order_by("name")[:2])
        data_centers = list(DataCenter.objects.order_by("name")[:2])
        if len(organizations) != 1 or len(data_centers) != 1:
            raise CommandError(
                "Could not safely determine the NTI organization/datacenter. "
                "Add a reference NETBOTZ device or keep exactly one organization and datacenter."
            )
        return organizations[0], data_centers[0], None, None

    def handle(self, *args, **options):
        from decouple import config

        community = config("NTI_SNMP_COMMUNITY", default="")
        if not community and not options["dry_run"]:
            raise CommandError("Set NTI_SNMP_COMMUNITY in the environment before enabling NTI polling.")

        device_type, _ = DeviceType.objects.get_or_create(
            code=DEVICE_TYPE_CODE,
            defaults={"name": "NetBotz / Environmental Monitor", "category": "ENVIRONMENT"},
        )
        organization, data_center, room, rack = self._resolve_location(device_type)
        if options["dry_run"]:
            self.stdout.write(
                f"Would onboard NTI ENVIROMUX-16D at {DEVICE_IP} in "
                f"{organization.code}/{data_center.code}: {len(METRICS)} metrics, "
                f"{len(OID_MAPPINGS)} OID mappings, {len(TRAP_MAPPINGS)} trap mappings."
            )
            return

        with transaction.atomic():
            vendor, _ = Vendor.objects.update_or_create(
                code=VENDOR_CODE,
                defaults={"name": "Network Technologies Inc."},
            )
            model, _ = DeviceModel.objects.update_or_create(
                vendor=vendor,
                model_number=MODEL_NUMBER,
                defaults={
                    "device_type": device_type,
                    "name": "NTI ENVIROMUX-16D Environmental Monitoring System",
                    "description": "16D environmental monitoring appliance; identity verified from SNMP sysObjectID and deviceModel.",
                },
            )
            device, created = Device.objects.update_or_create(
                ip_address=DEVICE_IP,
                defaults={
                    "organization": organization,
                    "data_center": data_center,
                    "room": room,
                    "rack": rack,
                    "device_type": device_type,
                    "device_model": model,
                    "name": "NTI ENVIROMUX-16D",
                    "code": "NTI-10-156-0-155",
                    "status": DeviceStatus.UNKNOWN,
                    "is_active": True,
                    "metadata": {
                        "manufacturer": "Network Technologies Inc.",
                        "model": MODEL_NUMBER,
                        "sys_object_id": PRODUCT_OID,
                        "firmware_version": "2.53",
                        "onboarding_source": "live SNMPv2c read; mibs/onebank/nti/enviromux-2d-v1-24.mib",
                        "nti_digital_inputs": [
                            {"index": index, "label": label, "normal_value": normal_value}
                            for index, label, normal_value in DIGITAL_INPUTS
                        ],
                    },
                },
            )

            for item in METRICS:
                MetricDefinition.objects.update_or_create(
                    code=item["code"],
                    defaults={**item, "is_active": True},
                )
            metrics_by_code = {metric.code: metric for metric in MetricDefinition.objects.filter(code__in=[row[0] for row in OID_MAPPINGS])}
            for code, oid, data_type, scale in OID_MAPPINGS:
                SNMPOIDMapping.objects.update_or_create(
                    device_type=device_type,
                    vendor=vendor,
                    device_model=model,
                    metric=metrics_by_code[code],
                    defaults={
                        "oid": oid,
                        "data_type": data_type,
                        "scale_factor": Decimal(scale),
                        "offset_value": Decimal("0"),
                        "is_active": True,
                    },
                )

            for item in TRAP_MAPPINGS:
                SNMPTrapOIDMapping.objects.update_or_create(
                    device_type=device_type,
                    vendor=vendor,
                    device_model=model,
                    trap_oid=item["trap_oid"],
                    defaults={
                        "event_code": item["event_code"],
                        "event_name": item["event_name"],
                        "severity": item["severity"],
                        "message_template": item["message_template"],
                        "create_alert": True,
                        "is_active": True,
                    },
                )

            DeviceProtocolConfig.objects.update_or_create(
                device=device,
                protocol=ProtocolType.SNMP,
                host=DEVICE_IP,
                port=161,
                defaults={
                    "timeout_seconds": 5,
                    "retry_count": 2,
                    "is_primary": True,
                    "is_enabled": True,
                    "extra_config": {"snmp_version": "v2c", "source": "verified live SNMPv2c read"},
                },
            )
            DeviceCredential.objects.update_or_create(
                device=device,
                protocol=ProtocolType.SNMP,
                defaults={
                    "snmp_version": SNMPVersion.V2C,
                    "snmp_community_encrypted": encrypt_secret(community),
                    "is_active": True,
                },
            )
            profile, _ = PollingProfile.objects.get_or_create(
                name="SNMP Critical 60s",
                protocol=ProtocolType.SNMP,
                defaults={
                    "priority": PollingPriority.HIGH,
                    "interval_seconds": 60,
                    "timeout_seconds": 5,
                    "retry_count": 2,
                    "stale_after_seconds": 180,
                    "is_active": True,
                },
            )
            DevicePollingConfig.objects.update_or_create(
                device=device,
                defaults={
                    "polling_profile": profile,
                    "is_enabled": True,
                    "next_poll_at": timezone.now(),
                    "consecutive_failures": 0,
                    "last_error_message": "",
                },
            )
            SNMPTrapSource.objects.update_or_create(
                source_ip=DEVICE_IP,
                data_center=data_center,
                defaults={
                    "organization": organization,
                    "device": device,
                    "is_enabled": True,
                    "description": "NTI ENVIROMUX-16D at 10.156.0.155; MIB-defined sensor notifications.",
                },
            )

        self.stdout.write(self.style.SUCCESS(f"NTI device {'created' if created else 'updated'}: {device.name} ({DEVICE_IP})"))
        self.stdout.write(f"Vendor/model: {vendor.name} / {model.model_number}")
        self.stdout.write(f"Frontend telemetry OIDs: {len(OID_MAPPINGS)}; trap OID mappings: {len(TRAP_MAPPINGS)}")
        self.stdout.write("SNMPv2c community is encrypted at rest; polling and trap source are enabled.")
