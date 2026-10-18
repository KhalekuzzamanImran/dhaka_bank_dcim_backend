import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.devices.models import (
    Device,
    DeviceProtocolConfig,
    ModbusGatewayConnectionStatus,
    ModbusGatewayWriteRequest,
    ModbusGatewayWriteStatus,
    ProtocolType,
)


class Command(BaseCommand):
    help = "Set the confirmed 65 Ah capacity on One Bank's Acrel battery monitor via its reverse Modbus gateway."

    def handle(self, *args, **options):
        device = Device.objects.filter(code="BATTERY-01", is_active=True).first()
        if not device:
            raise CommandError("Active One Bank battery device BATTERY-01 was not found.")
        config = DeviceProtocolConfig.objects.filter(
            device=device,
            protocol=ProtocolType.MODBUS_RTU_GATEWAY,
            is_enabled=True,
        ).select_related("modbus_gateway").first()
        if not config or not config.modbus_gateway:
            raise CommandError("BATTERY-01 has no enabled reverse Modbus gateway configuration.")
        if config.modbus_gateway.connection_status != ModbusGatewayConnectionStatus.CONNECTED:
            raise CommandError(
                f"Gateway {config.modbus_gateway.code} is not connected; current status is "
                f"{config.modbus_gateway.connection_status}. No write was sent."
            )
        if ModbusGatewayWriteRequest.objects.filter(
            gateway=config.modbus_gateway,
            status__in=[ModbusGatewayWriteStatus.PENDING, ModbusGatewayWriteStatus.PROCESSING],
        ).exists():
            raise CommandError("Another gateway write is pending or processing. No write was queued.")

        slave_id = int((config.extra_config or {}).get("slave_id", 1))
        request = ModbusGatewayWriteRequest.objects.create(
            gateway=config.modbus_gateway,
            device=device,
            register_address=23,
            register_value=65,
            slave_id=slave_id,
        )
        self.stdout.write("Queued Acrel capacity setting: register 23 = 65 Ah; waiting for verified read-back…")

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            request.refresh_from_db()
            if request.status in {ModbusGatewayWriteStatus.SUCCEEDED, ModbusGatewayWriteStatus.FAILED}:
                break
            time.sleep(0.5)

        request.refresh_from_db()
        if request.status == ModbusGatewayWriteStatus.SUCCEEDED and request.response_value == 65:
            self.stdout.write(self.style.SUCCESS(
                f"Verified: Acrel register 23 now reads {request.response_value} Ah "
                f"(completed {timezone.localtime(request.completed_at).strftime('%Y-%m-%d %H:%M:%S %Z')})."
            ))
            return
        if request.status == ModbusGatewayWriteStatus.FAILED:
            raise CommandError(f"Gateway write failed: {request.error_message}")
        raise CommandError(
            f"Timed out waiting for gateway response; request {request.pk} remains {request.status}. "
            "Check again before retrying to avoid a duplicate write."
        )
