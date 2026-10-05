from __future__ import annotations

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.devices.models import DeviceModel, DeviceType, Vendor
from apps.traps.models import SNMPTrapOIDMapping
from apps.traps.services.mib_registry import parse_mib_file


MIB_PATH = Path(__file__).resolve().parents[4] / "mibs" / "onebank" / "ups" / "NetVision-8.11.mib"

# These are explicit restoration notifications in the NetVision MIB. The
# receiver resolves open alerts by matching resolves_event_code to event_code.
RESOLVES = {
    "upsTrapPowerRestored": "UPS_TRAP_ON_BATTERY",
    "upsTrapComEstablished": "UPS_TRAP_COMMUNICATION_LOST",
    "upsTrapEmdTempNotLow": "UPS_TRAP_EMD_TEMP_LOW",
    "upsTrapEmdTempNotHigh": "UPS_TRAP_EMD_TEMP_HIGH",
    "upsTrapEmdHumidityNotLow": "UPS_TRAP_EMD_HUMIDITY_LOW",
    "upsTrapEmdHumidityNotHigh": "UPS_TRAP_EMD_HUMIDITY_HIGH",
    "upsTrapEmdFirstInputRestored": "UPS_TRAP_EMD_FIRST_INPUT_ACTIVE",
    "upsTrapEmdSecondInputRestored": "UPS_TRAP_EMD_SECOND_INPUT_ACTIVE",
}

EVENT_CODE_ALIASES = {
    # Both notifications signal the same persistent on-battery condition.
    "upsTrapOnBatteryPower": "UPS_TRAP_ON_BATTERY",
}

EVENT_NAME_OVERRIDES = {
    "upsTrapComEstablished": "UPS Communication Established",
    "upsTrapShutdwonCancelled": "UPS Shutdown Cancelled",
    "upsTrapBatteryTestfailed": "UPS Battery Test Failed",
    "upsTrapShutdownrequest": "UPS Shutdown Request",
    "upsTrapEmdTempLow": "UPS EMD Temperature Low",
    "upsTrapEmdTempNotLow": "UPS EMD Temperature Restored from Low",
    "upsTrapEmdTempHigh": "UPS EMD Temperature High",
    "upsTrapEmdTempNotHigh": "UPS EMD Temperature Restored from High",
}


def _humanize_symbol(symbol: str) -> str:
    name = re.sub(r"^upsTrap", "", symbol)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).strip()


def _trap_rows():
    module_name, definitions, _ = parse_mib_file(MIB_PATH)
    if module_name != "SOCOMECUPS-MIB":
        raise CommandError(f"Unexpected MIB module in {MIB_PATH}: {module_name}")

    traps = [item for item in definitions if item.symbol.startswith("upsTrap")]
    if len(traps) != 42:
        raise CommandError(f"Expected 42 UPS traps in {MIB_PATH}, found {len(traps)}")

    rows = []
    for trap in traps:
        description = (trap.description or "").strip()
        severity_match = re.match(r"^(INFORMATION|WARNING|CRITICAL)\s*:\s*", description, re.IGNORECASE)
        if not severity_match:
            raise CommandError(f"Trap {trap.symbol} has no recognized severity in the MIB")
        severity = "INFO" if severity_match.group(1).upper() == "INFORMATION" else severity_match.group(1).upper()
        message = description[severity_match.end():].strip()
        rows.append({
            "trap_oid": trap.oid,
            "event_code": EVENT_CODE_ALIASES.get(
                trap.symbol,
                f"UPS_TRAP_{re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', trap.symbol.removeprefix('upsTrap')).upper()}",
            ),
            "event_name": EVENT_NAME_OVERRIDES.get(trap.symbol, f"UPS {_humanize_symbol(trap.symbol)}".strip()),
            "severity": severity,
            "message_template": message,
            "create_alert": severity != "INFO",
            "resolves_event_code": RESOLVES.get(trap.symbol),
            "is_active": True,
        })
    return rows


class Command(BaseCommand):
    help = "Seed Socomec Net Vision 8 UPS SNMP trap mappings from the OneBank MIB."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show the mappings without writing to the database.")

    def handle(self, *args, **options):
        device_type = DeviceType.objects.filter(code__iexact="UPS").first()
        vendor = Vendor.objects.filter(code__iexact="SOCOMEC").first()
        if not device_type:
            raise CommandError("UPS device type was not found; run the DCIM seed first.")
        if not vendor:
            raise CommandError("Socomec vendor (code SOCOMEC) was not found.")
        models = list(DeviceModel.objects.filter(device_type=device_type, vendor=vendor).order_by("model_number"))
        if not models:
            raise CommandError("No Socomec UPS device models were found to scope the trap mappings.")

        rows = _trap_rows()
        if options["dry_run"]:
            self.stdout.write(
                f"Would seed {len(rows)} Net Vision UPS trap mappings for {len(models)} Socomec UPS model(s)."
            )
            return

        created = updated = 0
        with transaction.atomic():
            for device_model in models:
                for row in rows:
                    _, was_created = SNMPTrapOIDMapping.objects.update_or_create(
                        device_type=device_type,
                        vendor=vendor,
                        device_model=device_model,
                        trap_oid=row["trap_oid"],
                        defaults={key: value for key, value in row.items() if key != "trap_oid"},
                    )
                    created += int(was_created)
                    updated += int(not was_created)

        self.stdout.write(self.style.SUCCESS("Socomec Net Vision UPS trap mapping seed completed."))
        self.stdout.write(f"Models: {len(models)}; mappings created: {created}; updated: {updated}")
        self.stdout.write("INFO traps are recorded as device events; WARNING/CRITICAL traps create alerts.")
