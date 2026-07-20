from django.core.management.base import BaseCommand, CommandError

from apps.alerts.models import AlertRule, AlertSeverity
from apps.common.models import StatusChoices
from apps.devices.models import DeviceType
from apps.organizations.models import Organization
from apps.telemetry.models import MetricDefinition


PAC_RULES = (
    ("pac_general_alarm", "PAC General Alarm", AlertSeverity.CRITICAL),
    ("pac_room_high_temperature_alarm", "PAC Room High Temperature Alarm", AlertSeverity.CRITICAL),
    ("pac_room_low_temperature_alarm", "PAC Room Low Temperature Alarm", AlertSeverity.WARNING),
    ("pac_room_high_humidity_alarm", "PAC Room High Humidity Alarm", AlertSeverity.WARNING),
    ("pac_room_low_humidity_alarm", "PAC Room Low Humidity Alarm", AlertSeverity.WARNING),
    ("pac_airflow_alarm", "PAC Airflow Alarm", AlertSeverity.CRITICAL),
    ("pac_water_leak_alarm", "PAC Water Leak Alarm", AlertSeverity.CRITICAL),
    ("pac_filter_alarm", "PAC Filter Alarm", AlertSeverity.WARNING),
    ("pac_phase_sequence_alarm", "PAC Phase Sequence Alarm", AlertSeverity.CRITICAL),
    ("pac_smoke_fire_alarm", "PAC Smoke Fire Alarm", AlertSeverity.CRITICAL),
    ("pac_lan_alarm", "PAC LAN Alarm", AlertSeverity.CRITICAL),
    ("pac_emergency_power_alarm", "PAC Emergency Power Alarm", AlertSeverity.CRITICAL),
)


class Command(BaseCommand):
    help = "Create or update PAC alarm rules for an organization."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization-code",
            dest="organization_code",
            default=None,
            help="Organization code. Required when more than one active organization exists.",
        )

    def _resolve_organization(self, organization_code):
        if organization_code:
            organization = Organization.objects.filter(
                code=organization_code,
                status=StatusChoices.ACTIVE,
            ).first()
            if not organization:
                raise CommandError(
                    f"Active organization with code '{organization_code}' was not found."
                )
            return organization

        organizations = list(
            Organization.objects.filter(status=StatusChoices.ACTIVE).order_by("created_at")
        )
        if len(organizations) == 1:
            return organizations[0]
        if not organizations:
            raise CommandError("No active organization was found. Provide --organization-code.")
        raise CommandError(
            "Multiple active organizations exist. Provide --organization-code."
        )

    def handle(self, *args, **options):
        organization = self._resolve_organization(options.get("organization_code"))
        device_type = DeviceType.objects.filter(code__iexact="PAC").first()
        if not device_type:
            raise CommandError("Device type 'PAC' was not found.")

        created = 0
        updated = 0
        missing = []

        for metric_code, name, severity in PAC_RULES:
            metric = MetricDefinition.objects.filter(
                code=metric_code,
                is_active=True,
            ).first()
            if not metric:
                missing.append(metric_code)
                continue

            rule = AlertRule.objects.filter(
                organization=organization,
                data_center=None,
                device_type=device_type,
                device=None,
                metric=metric,
                operator="EQ",
                threshold_integer=1,
                threshold_float=None,
                threshold_boolean=None,
                threshold_text=None,
            ).order_by("created_at").first()

            defaults = {
                "name": name,
                "severity": severity,
                "duration_seconds": 0,
                "is_active": True,
                "message_template": f"{name} is active",
            }
            if rule:
                for field, value in defaults.items():
                    setattr(rule, field, value)
                rule.save()
                updated += 1
            else:
                AlertRule.objects.create(
                    organization=organization,
                    data_center=None,
                    device_type=device_type,
                    device=None,
                    metric=metric,
                    operator="EQ",
                    threshold_integer=1,
                    **defaults,
                )
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"PAC alert rules seeded for {organization.code}. "
                f"created={created} updated={updated}"
            )
        )
        if missing:
            raise CommandError(
                "Required PAC alarm metrics are missing or inactive: "
                + ", ".join(missing)
            )
