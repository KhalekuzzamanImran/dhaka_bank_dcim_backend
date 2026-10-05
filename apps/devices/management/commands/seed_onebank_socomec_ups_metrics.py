from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.devices.models import DeviceModel, DeviceType, SNMPOIDMapping, Vendor
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition


# These RFC 1628 UPS-MIB objects were verified against the One Bank Socomec
# Net Vision v6.30 walk in snmpwalks/onebank/socomec_ups/snmp_walk.txt.
# The bundled NetVision-8.11.mib describes enterprise branch .4555.1.1.7,
# which is not exposed by the currently onboarded v6.30 card.
UPS_METRICS = [
    {
        "code": "ups_battery_capacity_percent",
        "name": "UPS Battery Capacity",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "%",
        "description": "Estimated battery charge remaining; RFC 1628 upsEstimatedChargeRemaining.",
        "oid": "1.3.6.1.2.1.33.1.2.4.0",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_battery_charge",
        "name": "UPS Battery Charge",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "%",
        "description": "Compatibility alias for RFC 1628 estimated battery charge remaining.",
        "oid": "1.3.6.1.2.1.33.1.2.4.0",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_battery_voltage",
        "name": "UPS Battery Voltage",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "V",
        "description": "RFC 1628 upsBatteryVoltage, reported in 0.1 V DC units.",
        "oid": "1.3.6.1.2.1.33.1.2.5.0",
        "mapping_data_type": "INTEGER",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_battery_temperature_celsius",
        "name": "UPS Battery Temperature",
        "category": MetricCategory.ENVIRONMENT,
        "data_type": MetricDataType.FLOAT,
        "unit": "°C",
        "description": "Battery temperature reported by RFC 1628 upsBatteryTemperature.",
        "oid": "1.3.6.1.2.1.33.1.2.7.0",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_abnormal_conditions",
        "name": "UPS Active Alarm Count",
        "category": MetricCategory.ALARM,
        "data_type": MetricDataType.INTEGER,
        "unit": "count",
        "description": "RFC 1628 upsAlarmsPresent count; positive values indicate active alarms.",
        "oid": "1.3.6.1.2.1.33.1.6.1.0",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_input_voltage",
        "name": "UPS Input Voltage (Line 1)",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "V",
        "description": "Line 1 input voltage; the detail view uses phase-specific metrics when present.",
        "oid": "1.3.6.1.2.1.33.1.3.3.1.3.1",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_input_frequency",
        "name": "UPS Input Frequency",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "Hz",
        "description": "Line 1 input frequency from the RFC 1628 input table.",
        "oid": "1.3.6.1.2.1.33.1.3.3.1.2.1",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_output_voltage",
        "name": "UPS Output Voltage (Line 1)",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "V",
        "description": "Line 1 output voltage; the detail view uses phase-specific metrics when present.",
        "oid": "1.3.6.1.2.1.33.1.4.4.1.2.1",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_output_status",
        "name": "UPS Output Source Status",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.INTEGER,
        "unit": "state",
        "description": "RFC 1628 upsOutputSource enum: normal=3, bypass=4, battery=5.",
        "oid": "1.3.6.1.2.1.33.1.4.1.0",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_output_frequency",
        "name": "UPS Output Frequency",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "Hz",
        "description": "RFC 1628 UPS output frequency.",
        "oid": "1.3.6.1.2.1.33.1.4.2.0",
        "mapping_data_type": "INTEGER",
    },
    {
        "code": "ups_output_current",
        "name": "UPS Output Current (Line 1)",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "A",
        "description": "Line 1 output current; see phase-specific metrics for all output lines.",
        "oid": "1.3.6.1.2.1.33.1.4.4.1.3.1",
        "mapping_data_type": "INTEGER",
        "scale_factor": "0.1",
    },
    {
        "code": "ups_output_kva_capacity",
        "name": "UPS Rated Output Capacity",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "kVA",
        "description": "RFC 1628 configured nominal output VA converted to kVA.",
        "oid": "1.3.6.1.2.1.33.1.9.5.0",
        "mapping_data_type": "INTEGER",
        "scale_factor": "0.001",
    },
]

DERIVED_METRICS = [
    {
        "code": "ups_load_percent",
        "name": "UPS Average Output Load",
        "category": MetricCategory.POWER,
        "data_type": MetricDataType.FLOAT,
        "unit": "%",
        "description": "Average of the three RFC 1628 per-phase output load readings.",
    },
]

for phase in (1, 2, 3):
    UPS_METRICS.extend(
        [
            {
                "code": f"ups_input_l{phase}_voltage",
                "name": f"UPS Input L{phase} Voltage",
                "category": MetricCategory.POWER,
                "data_type": MetricDataType.FLOAT,
                "unit": "V",
                "description": f"RFC 1628 UPS input line {phase} voltage.",
                "oid": f"1.3.6.1.2.1.33.1.3.3.1.3.{phase}",
                "mapping_data_type": "INTEGER",
            },
            {
                "code": f"ups_input_l{phase}_current",
                "name": f"UPS Input L{phase} Current",
                "category": MetricCategory.POWER,
                "data_type": MetricDataType.FLOAT,
                "unit": "A",
                "description": f"RFC 1628 UPS input line {phase} current.",
                "oid": f"1.3.6.1.2.1.33.1.3.3.1.4.{phase}",
                "mapping_data_type": "INTEGER",
                "scale_factor": "0.1",
            },
            {
                "code": f"ups_output_l{phase}_voltage",
                "name": f"UPS Output L{phase} Voltage",
                "category": MetricCategory.POWER,
                "data_type": MetricDataType.FLOAT,
                "unit": "V",
                "description": f"RFC 1628 UPS output line {phase} voltage.",
                "oid": f"1.3.6.1.2.1.33.1.4.4.1.2.{phase}",
                "mapping_data_type": "INTEGER",
            },
            {
                "code": f"ups_output_l{phase}_current",
                "name": f"UPS Output L{phase} Current",
                "category": MetricCategory.POWER,
                "data_type": MetricDataType.FLOAT,
                "unit": "A",
                "description": f"RFC 1628 UPS output line {phase} current.",
                "oid": f"1.3.6.1.2.1.33.1.4.4.1.3.{phase}",
                "mapping_data_type": "INTEGER",
                "scale_factor": "0.1",
            },
            {
                "code": f"ups_output_l{phase}_percent_load",
                "name": f"UPS Output L{phase} Load",
                "category": MetricCategory.POWER,
                "data_type": MetricDataType.FLOAT,
                "unit": "%",
                "description": f"RFC 1628 UPS output line {phase} percent load.",
                "oid": f"1.3.6.1.2.1.33.1.4.4.1.4.{phase}",
                "mapping_data_type": "INTEGER",
            },
        ]
    )


class Command(BaseCommand):
    help = "Seed One Bank Socomec UPS telemetry metrics and RFC 1628 OID mappings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the models and mappings that would be seeded without writing to the database.",
        )

    def handle(self, *args, **options):
        device_type = DeviceType.objects.filter(code__iexact="UPS").first()
        vendor = Vendor.objects.filter(code__iexact="SOCOMEC").first()
        if not device_type:
            raise CommandError("UPS device type was not found; run the DCIM seed first.")
        if not vendor:
            raise CommandError("Socomec vendor (code SOCOMEC) was not found.")

        models = list(
            DeviceModel.objects.filter(device_type=device_type, vendor=vendor).order_by("model_number")
        )
        if not models:
            raise CommandError("No Socomec UPS device models were found to scope the OID mappings.")

        if options["dry_run"]:
            metrics_to_seed = UPS_METRICS + DERIVED_METRICS
            self.stdout.write(
                f"Would seed {len(metrics_to_seed)} metrics, {len(UPS_METRICS) * len(models)} "
                f"direct model-specific mappings, and {len(DERIVED_METRICS)} derived metric(s) "
                f"for {len(models)} Socomec UPS model(s)."
            )
            return

        metric_created = metric_updated = mapping_created = mapping_updated = 0
        with transaction.atomic():
            for item in UPS_METRICS + DERIVED_METRICS:
                defaults = {
                    "name": item["name"],
                    "category": item["category"],
                    "data_type": item["data_type"],
                    "unit": item["unit"],
                    "description": item["description"],
                    "is_active": True,
                }
                metric, created = MetricDefinition.objects.update_or_create(
                    code=item["code"], defaults=defaults
                )
                metric_created += int(created)
                metric_updated += int(not created)

                if not item.get("oid"):
                    continue

                for device_model in models:
                    mapping_defaults = {
                        "oid": item["oid"],
                        "data_type": item["mapping_data_type"],
                        "scale_factor": Decimal(item.get("scale_factor", "1")),
                        "offset_value": Decimal(item.get("offset_value", "0")),
                        "is_active": True,
                    }
                    _, created = SNMPOIDMapping.objects.update_or_create(
                        device_type=device_type,
                        vendor=vendor,
                        device_model=device_model,
                        metric=metric,
                        defaults=mapping_defaults,
                    )
                    mapping_created += int(created)
                    mapping_updated += int(not created)

        self.stdout.write(self.style.SUCCESS("One Bank Socomec UPS metric seed completed."))
        self.stdout.write(f"Socomec UPS models: {len(models)}")
        self.stdout.write(f"Metrics created/updated: {metric_created}/{metric_updated}")
        self.stdout.write(f"OID mappings created/updated: {mapping_created}/{mapping_updated}")
        self.stdout.write("Derived metrics: ups_load_percent = mean of the three output phase loads")
        self.stdout.write(
            "Not mapped: runtime (the device reports 65535/unavailable), "
            "bad battery-pack count, and redundancy (no matching RFC 1628 object)."
        )
