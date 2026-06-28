from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.devices.models import Device, DeviceCredential, DeviceProtocolConfig, DevicePollingConfig, PollingProfile, ProtocolType, SNMPVersion
from collectors.snmp_collector.security import encrypt_secret


class Command(BaseCommand):
    help = "Create or update SNMP protocol config and encrypted SNMP credentials for a device."

    def add_arguments(self, parser):
        parser.add_argument("device_id")
        parser.add_argument("--host", required=True)
        parser.add_argument("--port", type=int, default=161)
        parser.add_argument("--version", choices=[SNMPVersion.V1, SNMPVersion.V2C, SNMPVersion.V3], default=SNMPVersion.V2C)
        parser.add_argument("--community")
        parser.add_argument("--username")
        parser.add_argument("--auth-protocol", default="SHA256")
        parser.add_argument("--auth-key")
        parser.add_argument("--priv-protocol", default="AES128")
        parser.add_argument("--priv-key")
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--timeout", type=int, default=5)
        parser.add_argument("--retries", type=int, default=2)

    def handle(self, *args, **options):
        device = Device.objects.get(pk=options["device_id"])
        if options["version"] in {SNMPVersion.V1, SNMPVersion.V2C} and not options.get("community"):
            raise CommandError("--community is required for SNMP v1/v2c")
        if options["version"] == SNMPVersion.V3 and not options.get("username"):
            raise CommandError("--username is required for SNMP v3")

        DeviceProtocolConfig.objects.update_or_create(
            device=device,
            protocol=ProtocolType.SNMP,
            host=options["host"],
            port=options["port"],
            defaults={
                "timeout_seconds": options["timeout"],
                "retry_count": options["retries"],
                "is_primary": True,
                "is_enabled": True,
            },
        )
        credential, _ = DeviceCredential.objects.update_or_create(
            device=device,
            protocol=ProtocolType.SNMP,
            defaults={
                "username": options.get("username"),
                "snmp_version": options["version"],
                "snmp_community_encrypted": encrypt_secret(options.get("community")),
                "snmp_v3_auth_protocol": options.get("auth_protocol"),
                "snmp_v3_auth_key_encrypted": encrypt_secret(options.get("auth_key")),
                "snmp_v3_priv_protocol": options.get("priv_protocol"),
                "snmp_v3_priv_key_encrypted": encrypt_secret(options.get("priv_key")),
                "is_active": True,
            },
        )
        profile, _ = PollingProfile.objects.get_or_create(
            name=f"SNMP {options['interval']}s",
            protocol=ProtocolType.SNMP,
            defaults={
                "interval_seconds": options["interval"],
                "timeout_seconds": options["timeout"],
                "retry_count": options["retries"],
                "stale_after_seconds": max(options["interval"] * 3, 180),
                "is_active": True,
            },
        )
        DevicePollingConfig.objects.update_or_create(
            device=device,
            defaults={"polling_profile": profile, "is_enabled": True, "next_poll_at": timezone.now()},
        )
        self.stdout.write(self.style.SUCCESS(f"SNMP configured for {device.name}. Secrets are encrypted when FIELD_ENCRYPTION_KEY is stable."))
