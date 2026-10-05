from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.devices.models import (
    Device,
    DeviceCredential,
    DevicePollingConfig,
    DeviceProtocolConfig,
    DeviceStatus,
    PollingPriority,
    PollingProfile,
    ProtocolType,
    SNMPVersion,
    SNMPOIDMapping,
)
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition
from collectors.snmp_collector.security import encrypt_secret


DEVICE_IP = "10.156.0.156"

# Verified with a live SNMPv2c walk of the Liebert/Emerson agent at DEVICE_IP.
# Keep this list limited to metrics read by the PAC frontend and returned by
# this specific unit. Temperature and relative-humidity columns are tenths.
PAC_METRICS = (
    {
        "code": "pac_room_temperature",
        "name": "PAC Room Temperature",
        "category": MetricCategory.ENVIRONMENT,
        "data_type": MetricDataType.FLOAT,
        "unit": "°C",
        "description": "Liebert return-air temperature sensor (row 3), reported in 0.1 °C.",
        "oid": "1.3.6.1.4.1.476.1.42.3.4.1.3.3.1.50.3",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "pac_room_humidity",
        "name": "PAC Room Humidity",
        "category": MetricCategory.ENVIRONMENT,
        "data_type": MetricDataType.FLOAT,
        "unit": "%",
        "description": "Liebert relative-humidity sensor (row 1), reported in 0.1% RH.",
        "oid": "1.3.6.1.4.1.476.1.42.3.4.2.2.3.1.50.1",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "pac_cooling_setpoint",
        "name": "PAC Cooling Setpoint",
        "category": MetricCategory.COOLING,
        "data_type": MetricDataType.FLOAT,
        "unit": "°C",
        "description": "Liebert air-temperature setpoint (row 1), reported in 0.1 °C.",
        "oid": "1.3.6.1.4.1.476.1.42.3.4.1.3.3.1.53.1",
        "mapping_data_type": "integer",
        "scale_factor": "0.1",
    },
    {
        "code": "pac_fan_speed",
        "name": "PAC Fan Speed",
        "category": MetricCategory.COOLING,
        "data_type": MetricDataType.FLOAT,
        "unit": "%",
        "description": "Liebert fan capacity in percent.",
        "oid": "1.3.6.1.4.1.476.1.42.3.4.3.16.0",
        "mapping_data_type": "integer",
    },
    {
        "code": "pac_running_status",
        "name": "PAC Running Status",
        "category": MetricCategory.STATUS,
        "data_type": MetricDataType.INTEGER,
        "unit": "state",
        "description": "Liebert system state enum; the live unit reports 1 while in normal operation.",
        "oid": "1.3.6.1.4.1.476.1.42.3.4.3.1.0",
        "mapping_data_type": "integer",
    },
    {
        "code": "pac_general_alarm",
        "name": "PAC Active Alarm Count",
        "category": MetricCategory.ALARM,
        "data_type": MetricDataType.INTEGER,
        "unit": "count",
        "description": "Liebert active-condition count; the live unit reports 0. The frontend uses this value as its general-alarm indicator.",
        "oid": "1.3.6.1.4.1.476.1.42.3.2.2.0",
        "mapping_data_type": "integer",
    },
    {
        "code": "pac_compressor_1_status",
        "name": "PAC Compressor 1 Status",
        "category": MetricCategory.STATUS,
        "data_type": MetricDataType.BOOLEAN,
        "unit": "state",
        "description": "Compressor 1 state from the live-verified PAC SNMP report OID; on/off string normalized to boolean.",
        "oid": "1.3.6.1.4.1.476.1.42.3.9.20.1.20.1.2.1.5264.1",
        "mapping_data_type": "boolean",
    },
    {
        "code": "pac_compressor_2_status",
        "name": "PAC Compressor 2 Status",
        "category": MetricCategory.STATUS,
        "data_type": MetricDataType.BOOLEAN,
        "unit": "state",
        "description": "Compressor 2 state from the live-verified PAC SNMP report OID; on/off string normalized to boolean.",
        "oid": "1.3.6.1.4.1.476.1.42.3.9.20.1.20.1.2.1.5264.2",
        "mapping_data_type": "boolean",
    },
)


class Command(BaseCommand):
    help = "Seed frontend-required, live-verified OID mappings for the One Bank Liebert PAC."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from decouple import config

        community = config("PAC_SNMP_COMMUNITY", default="")
        if not community and not options["dry_run"]:
            raise CommandError("Set PAC_SNMP_COMMUNITY in the environment before enabling PAC polling.")

        device = (
            Device.objects.select_related("device_type", "device_model")
            .filter(ip_address=DEVICE_IP, is_active=True)
            .first()
        )
        if device is None:
            raise CommandError(f"No active PAC device was found at {DEVICE_IP}.")
        if device.device_type.code.upper() != "PAC":
            raise CommandError(f"Device at {DEVICE_IP} has type {device.device_type.code}, not PAC.")
        if device.device_model is None:
            raise CommandError("The PAC needs a device model before model-specific OID mappings can be seeded.")

        if options["dry_run"]:
            self.stdout.write(
                f"Would seed {len(PAC_METRICS)} metric definitions and model-specific OID mappings "
                f"for {device.name} ({DEVICE_IP}; {device.device_model.model_number}), "
                "encrypt the SNMP v2c community, and enable 60-second polling."
            )
            for item in PAC_METRICS:
                self.stdout.write(f"{item['code']}: {item['oid']} (scale {item.get('scale_factor', '1')})")
            return

        created_metrics = 0
        created_mappings = 0
        with transaction.atomic():
            for item in PAC_METRICS:
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
                created_metrics += int(created)

                _, created = SNMPOIDMapping.objects.update_or_create(
                    device_type=device.device_type,
                    vendor=device.device_model.vendor,
                    device_model=device.device_model,
                    metric=metric,
                    defaults={
                        "oid": item["oid"],
                        "data_type": item["mapping_data_type"],
                        "scale_factor": item.get("scale_factor", "1"),
                        "offset_value": "0",
                        "is_active": True,
                    },
                )
                created_mappings += int(created)

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
                    "extra_config": {"source": "verified Liebert SNMPv2c read at 10.156.0.156"},
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
            polling_profile, _ = PollingProfile.objects.get_or_create(
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
                    "polling_profile": polling_profile,
                    "is_enabled": True,
                    "next_poll_at": timezone.now(),
                    "consecutive_failures": 0,
                    "last_error_message": "",
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(PAC_METRICS)} PAC metrics/mappings for {device.name} "
                f"({created_metrics} metric definitions created, {created_mappings} mappings created)."
            )
        )
        self.stdout.write("SNMP v2c endpoint enabled; community saved encrypted; polling scheduled every 60 seconds.")
