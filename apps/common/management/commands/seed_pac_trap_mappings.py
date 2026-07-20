from django.core.management.base import BaseCommand, CommandError

from apps.devices.models import DeviceModel, DeviceType, Vendor
from apps.traps.models import SNMPTrapOIDMapping, TrapSeverity


DEFAULT_VENDOR_NAME = "Schneider Electric / Uniflair"
DEFAULT_MODEL_NAME = "Uniflair AM LE UG40 DX"


class Command(BaseCommand):
    help = "Register the captured PAC alarm fired/restored trap OIDs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fired-trap-oid",
            required=True,
            help="The exact snmpTrapOID.0 captured for PAC_ALARM_FIRED.",
        )
        parser.add_argument(
            "--restored-trap-oid",
            required=True,
            help="The exact snmpTrapOID.0 captured for PAC_ALARM_RESTORED.",
        )
        parser.add_argument("--vendor-name", default=DEFAULT_VENDOR_NAME)
        parser.add_argument("--device-model-name", default=DEFAULT_MODEL_NAME)

    @staticmethod
    def _normalize_oid(value):
        value = str(value or "").strip().lstrip(".")
        if not value:
            raise CommandError("Trap OID cannot be empty.")
        return value

    def handle(self, *args, **options):
        fired_oid = self._normalize_oid(options["fired_trap_oid"])
        restored_oid = self._normalize_oid(options["restored_trap_oid"])
        if fired_oid == restored_oid:
            raise CommandError("Fired and restored trap OIDs must be different.")

        device_type = DeviceType.objects.filter(code__iexact="PAC").first()
        vendor = Vendor.objects.filter(name__iexact=options["vendor_name"]).first()
        device_model = DeviceModel.objects.filter(
            name__iexact=options["device_model_name"]
        ).first()
        if not device_type:
            raise CommandError("Device type 'PAC' was not found.")
        if not vendor:
            raise CommandError(f"Vendor '{options['vendor_name']}' was not found.")
        if not device_model:
            raise CommandError(
                f"Device model '{options['device_model_name']}' was not found."
            )

        mappings = (
            (
                "PAC_ALARM_FIRED",
                fired_oid,
                "PAC alarm fired",
                TrapSeverity.CRITICAL,
                "PAC alarm fired; confirmation polling will identify active alarm metrics.",
            ),
            (
                "PAC_ALARM_RESTORED",
                restored_oid,
                "PAC alarm restored",
                TrapSeverity.INFO,
                "PAC alarm restored; confirmation polling will resolve cleared alarm metrics.",
            ),
        )

        for event_code, trap_oid, event_name, severity, message in mappings:
            mapping, created = SNMPTrapOIDMapping.objects.update_or_create(
                device_type=device_type,
                vendor=vendor,
                device_model=device_model,
                event_code=event_code,
                defaults={
                    "trap_oid": trap_oid,
                    "event_name": event_name,
                    "severity": severity,
                    "message_template": message,
                    # PAC confirmation traps trigger a verification poll; they
                    # must not create a generic alert directly.
                    "create_alert": False,
                    "is_active": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} {mapping.event_code}: {trap_oid}"))
