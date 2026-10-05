from __future__ import annotations

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.datacenters.models import DataCenter
from apps.devices.models import (
    Device,
    DeviceModel,
    DeviceProtocolConfig,
    DeviceStatus,
    DeviceType,
    ProtocolType,
    SNMPOIDMapping,
    Vendor,
)
from apps.organizations.models import Organization
from apps.telemetry.models import MetricDefinition, MetricCategory, MetricDataType
from apps.traps.models import SNMPTrapSource


DEVICE_IP = "10.156.0.156"
WALK_PATH = Path(__file__).resolve().parents[4] / "snmpwalks" / "onebank" / "pac" / "snmp_walk.txt"
MIB_DIR = Path(__file__).resolve().parents[4] / "mibs" / "onebank" / "pac"
SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"


def _walk_identity():
    if not WALK_PATH.is_file():
        raise CommandError(f"PAC SNMP walk was not found: {WALK_PATH}")
    text = WALK_PATH.read_text(encoding="utf-8", errors="replace")
    sys_object_id = re.search(r"SNMPv2-MIB::sysObjectID\.0\s*=\s*OID:\s*(.+)", text)
    sys_descr = re.search(r"SNMPv2-MIB::sysDescr\.0\s*=\s*STRING:\s*(.+)", text)
    sys_uptime = re.search(r"sysUpTimeInstance\s*=\s*Timeticks:\s*\((\d+)\)", text)
    address_present = re.search(r"IP-MIB::ipAdEntAddr\.10\.156\.0\.156\s*=\s*IpAddress:\s*10\.156\.0\.156", text)
    if not address_present:
        raise CommandError(f"The PAC walk does not identify {DEVICE_IP}.")
    sys_object_id_value = sys_object_id.group(1).strip() if sys_object_id else None
    if sys_object_id_value and sys_object_id_value.startswith("SNMPv2-SMI::enterprises."):
        sys_object_id_value = sys_object_id_value.replace(
            "SNMPv2-SMI::enterprises.", "1.3.6.1.4.1.", 1
        )
    return {
        "sys_object_id": sys_object_id_value,
        "sys_descr": sys_descr.group(1).strip() if sys_descr else None,
        "sys_uptime_ticks": int(sys_uptime.group(1)) if sys_uptime else None,
    }


class Command(BaseCommand):
    help = "Onboard the One Bank PAC at 10.156.0.156 from its saved SNMP walk."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Validate source data and show the planned records.")

    def handle(self, *args, **options):
        identity = _walk_identity()
        if not MIB_DIR.is_dir():
            raise CommandError(f"PAC MIB directory was not found: {MIB_DIR}")
        usable_mibs = [path.name for path in MIB_DIR.iterdir() if path.is_file() and path.suffix.lower() in {".mib", ".txt"}]

        device_type = DeviceType.objects.filter(code__iexact="PAC").first()
        if not device_type:
            raise CommandError("Device type PAC was not found; seed the DCIM device types first.")

        reference_device = Device.objects.select_related("organization", "data_center", "room", "rack").filter(
            ip_address="10.156.0.157", is_active=True
        ).first()
        if reference_device:
            organization = reference_device.organization
            data_center = reference_device.data_center
            room, rack = reference_device.room, reference_device.rack
        else:
            organizations = list(Organization.objects.order_by("name"))
            data_centers = list(DataCenter.objects.order_by("name"))
            if len(organizations) != 1 or len(data_centers) != 1:
                raise CommandError("Could not safely determine the PAC organization and datacenter from existing records.")
            organization, data_center = organizations[0], data_centers[0]
            room = rack = None

        if options["dry_run"]:
            self.stdout.write(
                f"Would onboard PAC {DEVICE_IP} in {organization.code}/{data_center.code}; "
                f"sysObjectID={identity['sys_object_id'] or 'unknown'}; "
                f"vendor MIB files={len(usable_mibs)}."
            )
            if not usable_mibs:
                self.stdout.write("No text MIB file is present in mibs/onebank/pac; no PAC-specific metric or trap mappings will be invented.")
            return

        device_code = "PAC-10-156-0-156"
        with transaction.atomic():
            vendor, _ = Vendor.objects.get_or_create(
                code="VERTIV_LIEBERT",
                defaults={"name": "Vertiv / Liebert"},
            )
            device_model, _ = DeviceModel.objects.update_or_create(
                vendor=vendor,
                model_number="LIEBERT-PAC-UNSPECIFIED-476-1-42",
                defaults={
                    "device_type": device_type,
                    "name": "Liebert PAC (model unspecified)",
                    "description": (
                        "Vendor family identified from sysObjectID 1.3.6.1.4.1.476.1.42; "
                        "the available walk does not expose a specific PAC model."
                    ),
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
                    "device_model": device_model,
                    "name": "PAC 01",
                    "code": device_code,
                    "status": DeviceStatus.UNKNOWN,
                    "is_active": True,
                    "metadata": {
                        "onboarding_source": "snmpwalks/onebank/pac/snmp_walk.txt",
                        "sys_object_id": identity["sys_object_id"],
                        "sys_descr": identity["sys_descr"],
                        "sys_uptime_ticks_at_walk": identity["sys_uptime_ticks"],
                        "model_identification": "unknown_from_available_walk",
                        "sys_object_id_vendor_branch": "Vertiv / Liebert Global Products",
                        "mib_directory": "mibs/onebank/pac",
                        "vendor_mib_files_found": usable_mibs,
                    },
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
                    # The saved walk does not document its community string.
                    # Keep polling disabled until a verified credential is added.
                    "is_enabled": False,
                    "extra_config": {"source": "SNMPv2 walk; community not present in source files"},
                },
            )

            metric, _ = MetricDefinition.objects.update_or_create(
                code="snmp_sys_uptime",
                defaults={
                    "name": "SNMP System Uptime",
                    "category": MetricCategory.STATUS,
                    "data_type": MetricDataType.INTEGER,
                    "unit": "ticks",
                    "description": "SNMPv2-MIB sysUpTimeInstance observed in the PAC walk.",
                    "is_active": True,
                },
            )
            SNMPOIDMapping.objects.update_or_create(
                device_type=device_type,
                vendor=None,
                device_model=None,
                metric=metric,
                defaults={
                    "oid": SYS_UPTIME_OID,
                    "data_type": "integer",
                    "scale_factor": 1,
                    "offset_value": 0,
                    "is_active": True,
                },
            )
            SNMPTrapSource.objects.update_or_create(
                source_ip=DEVICE_IP,
                data_center=data_center,
                defaults={
                    "organization": organization,
                    "device": device,
                    "is_enabled": True,
                    "description": "One Bank PAC at 10.156.0.156; trap OIDs not defined in available PAC source files.",
                },
            )

        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"PAC device {action}: {device.name} ({DEVICE_IP})"))
        self.stdout.write(f"Organization/datacenter: {organization.code}/{data_center.code}")
        self.stdout.write(f"Vendor/model: {vendor.name} / {device_model.name}")
        self.stdout.write("Related records: SNMP endpoint, system uptime OID mapping, SNMP trap source.")
        self.stdout.write("Polling is disabled until a verified SNMP credential is configured.")
        if not usable_mibs:
            self.stdout.write("No usable text MIB was present; no PAC-specific metric or trap OIDs were fabricated.")
