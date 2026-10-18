from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.devices.models import Device, ModbusRegisterMapping
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition


class Command(BaseCommand):
    help = "Register the One Bank battery SOH metric and its Modbus gateway register mapping."

    def handle(self, *args, **options):
        device = (
            Device.objects.select_related("device_type", "device_model", "device_model__vendor")
            .filter(code="BATTERY-01")
            .first()
        )
        if device is None or device.device_model_id is None:
            raise CommandError("Battery 01 (BATTERY-01) and its device model must be onboarded first.")

        metric, _ = MetricDefinition.objects.update_or_create(
            code="battery_soh",
            defaults={
                "name": "Battery State of Health",
                "category": MetricCategory.POWER,
                "data_type": MetricDataType.FLOAT,
                "unit": "%",
                "description": "Acrel ABAT100 SOH1; one unsigned 16-bit Modbus register (2 bytes), scaled by 0.1%.",
                "is_active": True,
            },
        )

        _mapping, created = ModbusRegisterMapping.objects.update_or_create(
            device_type=device.device_type,
            device_model=device.device_model,
            vendor=device.device_model.vendor,
            metric=metric,
            defaults={
                "slave_id": 1,
                "function_code": 3,
                "register_address": 11131,
                "register_count": 1,
                "data_type": "uint16",
                "byte_order": "BIG",
                "word_order": "BIG",
                "scale_factor": Decimal("0.1"),
                "offset_value": Decimal("0"),
                "unit": "%",
                "is_active": True,
            },
        )

        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {metric.code} mapping for {device.name}: "
                "FC03 register 11131, count 1, uint16 (2 bytes), scale 0.1 %."
            )
        )
