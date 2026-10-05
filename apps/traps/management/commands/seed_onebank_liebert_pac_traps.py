from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.devices.models import Device
from apps.traps.models import SNMPTrapOIDMapping, TrapSeverity


DEVICE_IP = "10.156.0.156"

# Liebert GP registration notifications used by the PDX/PCW agent. The trap
# receiver recognizes these codes and polls the PAC alarm OID mappings to
# confirm whether alarm state opened or cleared before changing telemetry.
PAC_TRAPS = (
    {
        "trap_oid": "1.3.6.1.4.1.476.1.42.3.3.0.1",
        "event_code": "PAC_ALARM_FIRED",
        "event_name": "Liebert PAC condition added",
        "severity": TrapSeverity.WARNING,
        "message_template": "Liebert PAC condition added; confirm alarm state through SNMP polling.",
        "create_alert": False,
        "resolves_event_code": "",
    },
    {
        "trap_oid": "1.3.6.1.4.1.476.1.42.3.3.0.2",
        "event_code": "PAC_ALARM_RESTORED",
        "event_name": "Liebert PAC condition removed",
        "severity": TrapSeverity.INFO,
        "message_template": "Liebert PAC condition removed; confirm alarm state through SNMP polling.",
        "create_alert": False,
        "resolves_event_code": "",
    },
)


class Command(BaseCommand):
    help = "Seed Liebert PAC condition-added and condition-removed SNMP trap mappings."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        device = (
            Device.objects.select_related("device_type", "device_model__vendor")
            .filter(ip_address=DEVICE_IP, is_active=True)
            .first()
        )
        if device is None:
            raise CommandError(f"No active PAC device was found at {DEVICE_IP}.")
        if device.device_type.code.upper() != "PAC":
            raise CommandError(f"Device at {DEVICE_IP} has type {device.device_type.code}, not PAC.")
        if device.device_model is None:
            raise CommandError("The PAC needs a device model before model-specific trap mappings can be seeded.")

        if options["dry_run"]:
            self.stdout.write(
                f"Would seed {len(PAC_TRAPS)} Liebert condition notification mappings for "
                f"{device.name} ({DEVICE_IP}; {device.device_model.model_number})."
            )
            for row in PAC_TRAPS:
                self.stdout.write(f"{row['event_code']}: {row['trap_oid']}")
            return

        created = updated = 0
        with transaction.atomic():
            for row in PAC_TRAPS:
                _, was_created = SNMPTrapOIDMapping.objects.update_or_create(
                    device_type=device.device_type,
                    vendor=device.device_model.vendor,
                    device_model=device.device_model,
                    trap_oid=row["trap_oid"],
                    defaults={key: value for key, value in row.items() if key != "trap_oid"},
                )
                created += int(was_created)
                updated += int(not was_created)

        self.stdout.write(self.style.SUCCESS("Liebert PAC trap mapping seed completed."))
        self.stdout.write(f"Device model: {device.device_model.model_number}; created: {created}; updated: {updated}.")
        self.stdout.write("Mapped condition notifications invoke the PAC alarm-confirmation poller.")
