from django.core.management.base import BaseCommand

from apps.alerts.models import AlertSeverity
from apps.alerts.models import AlertRule
from apps.devices.models import DeviceType
from apps.organizations.models import Organization
from apps.telemetry.models import MetricDefinition


PAC_RULES = [
    ("pac_general_alarm", "PAC General Alarm", "pac_general_alarm", AlertSeverity.CRITICAL),
    ("pac_room_high_temperature_alarm", "PAC Room High Temperature Alarm", "pac_room_high_temperature_alarm", AlertSeverity.CRITICAL),
    ("pac_room_low_temperature_alarm", "PAC Room Low Temperature Alarm", "pac_room_low_temperature_alarm", AlertSeverity.WARNING),
    ("pac_room_high_humidity_alarm", "PAC Room High Humidity Alarm", "pac_room_high_humidity_alarm", AlertSeverity.WARNING),
    ("pac_room_low_humidity_alarm", "PAC Room Low Humidity Alarm", "pac_room_low_humidity_alarm", AlertSeverity.WARNING),
    ("pac_airflow_alarm", "PAC Airflow Alarm", "pac_airflow_alarm", AlertSeverity.CRITICAL),
    ("pac_water_leak_alarm", "PAC Water Leak Alarm", "pac_water_leak_alarm", AlertSeverity.CRITICAL),
    ("pac_filter_alarm", "PAC Filter Alarm", "pac_filter_alarm", AlertSeverity.WARNING),
    ("pac_smoke_fire_alarm", "PAC Smoke Fire Alarm", "pac_smoke_fire_alarm", AlertSeverity.CRITICAL),
    ("pac_emergency_power_alarm", "PAC Emergency Power Alarm", "pac_emergency_power_alarm", AlertSeverity.CRITICAL),
]


class Command(BaseCommand):
    help = "Seed default PAC alert rules safely."

    def handle(self, *args, **options):
        organizations = list(Organization.objects.all())
        pac_device_type = DeviceType.objects.filter(code="PAC").first()
        created = 0
        updated = 0
        skipped = []

        for _, label, metric_code, severity in PAC_RULES:
            metric = MetricDefinition.objects.filter(code=metric_code, is_active=True).first()
            if not metric:
                skipped.append(metric_code)
                continue
            for org in organizations:
                obj, was_created = AlertRule.objects.update_or_create(
                    organization=org,
                    data_center=None,
                    device_type=pac_device_type,
                    device=None,
                    metric=metric,
                    name=label,
                    defaults={
                        "operator": "EQ",
                        "threshold_integer": 1,
                        "threshold_float": None,
                        "threshold_boolean": None,
                        "threshold_text": None,
                        "severity": severity,
                        "duration_seconds": 0,
                        "is_active": True,
                        "message_template": f"{label} is active",
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"Alert rules seeded. created={created} updated={updated}"))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped missing metrics: {', '.join(skipped)}"))
