from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.devices.models import Device, DeviceProtocolConfig, DevicePollingConfig, PollingProfile, ProtocolType


class Command(BaseCommand):
    help = "Create or update Modbus TCP protocol config and polling profile for a device."

    def add_arguments(self, parser):
        parser.add_argument("device_id")
        parser.add_argument("--host", required=True)
        parser.add_argument("--port", type=int, default=502)
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--timeout", type=int, default=5)
        parser.add_argument("--retries", type=int, default=2)

    def handle(self, *args, **options):
        device = Device.objects.get(pk=options["device_id"])
        DeviceProtocolConfig.objects.update_or_create(
            device=device,
            protocol=ProtocolType.MODBUS_TCP,
            host=options["host"],
            port=options["port"],
            defaults={"timeout_seconds": options["timeout"], "retry_count": options["retries"], "is_primary": True, "is_enabled": True},
        )
        profile, _ = PollingProfile.objects.get_or_create(
            name=f"Modbus TCP {options['interval']}s",
            protocol=ProtocolType.MODBUS_TCP,
            defaults={"interval_seconds": options["interval"], "timeout_seconds": options["timeout"], "retry_count": options["retries"], "stale_after_seconds": max(options["interval"] * 3, 180), "is_active": True},
        )
        DevicePollingConfig.objects.update_or_create(device=device, defaults={"polling_profile": profile, "is_enabled": True, "next_poll_at": timezone.now()})
        self.stdout.write(self.style.SUCCESS(f"Modbus TCP configured for {device.name}."))
