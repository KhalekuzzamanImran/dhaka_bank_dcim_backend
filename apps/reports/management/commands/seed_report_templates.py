from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.common.models import StatusChoices
from apps.organizations.models import Organization
from apps.reports.seed_data import REPORT_TEMPLATE_SEEDS
from apps.reports.models import ReportTemplate


class Command(BaseCommand):
    help = "Seed or update the standard report templates for an organization."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization-code",
            dest="organization_code",
            default=None,
            help="Organization code to seed report templates for.",
        )
        parser.add_argument(
            "--organization-name",
            dest="organization_name",
            default=None,
            help="Optional organization name to seed report templates for when code is not provided.",
        )

    def _resolve_organization(self, *, organization_code: str | None, organization_name: str | None) -> Organization:
        if organization_code:
            organization = Organization.objects.filter(code=organization_code, status=StatusChoices.ACTIVE).first()
            if not organization:
                raise CommandError(f"Active organization with code '{organization_code}' was not found.")
            return organization

        if organization_name:
            organization = Organization.objects.filter(name=organization_name, status=StatusChoices.ACTIVE).first()
            if not organization:
                raise CommandError(f"Active organization with name '{organization_name}' was not found.")
            return organization

        active_organizations = list(Organization.objects.filter(status=StatusChoices.ACTIVE).order_by("created_at"))
        if len(active_organizations) == 1:
            return active_organizations[0]

        if not active_organizations:
            raise CommandError("No active organization was found. Provide --organization-code.")

        raise CommandError("Multiple active organizations exist. Provide --organization-code to seed report templates.")

    def handle(self, *args, **options):
        organization = self._resolve_organization(
            organization_code=options.get("organization_code"),
            organization_name=options.get("organization_name"),
        )

        created_count = 0
        updated_count = 0
        for template_def in REPORT_TEMPLATE_SEEDS:
            template, created = ReportTemplate.objects.update_or_create(
                organization=organization,
                code=template_def["code"],
                defaults={
                    "name": template_def["name"],
                    "description": template_def["description"],
                    "config": template_def["config"],
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created {template.code}"))
            else:
                updated_count += 1
                self.stdout.write(self.style.WARNING(f"Updated {template.code}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created_count} created and {updated_count} updated report templates for {organization.code}."
            )
        )
