from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.reports.services.retention import cleanup_expired_report_artifacts


class Command(BaseCommand):
    help = "Delete expired report artifacts and their stored files."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", default=False, help="List artifacts that would be deleted without removing them.")
        parser.add_argument("--batch-size", type=int, default=None, help="Maximum number of expired artifacts to process.")
        parser.add_argument("--before", dest="before", default=None, help="ISO datetime cutoff to evaluate retention against.")

    def _parse_before(self, value):
        if not value:
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            raise CommandError(f"Invalid ISO datetime value for --before: {value}")
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    def handle(self, *args, **options):
        now = self._parse_before(options.get("before"))
        result = cleanup_expired_report_artifacts(
            now=now,
            batch_size=options.get("batch_size"),
            dry_run=options.get("dry_run", False),
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Cleanup finished: examined={examined} deleted={deleted} failed={failed} skipped={skipped} disabled={disabled} dry_run={dry_run} batch_size={batch_size}".format(
                    examined=result.get("examined"),
                    deleted=result.get("deleted"),
                    failed=result.get("failed"),
                    skipped=result.get("skipped"),
                    disabled=result.get("disabled"),
                    dry_run=result.get("dry_run"),
                    batch_size=result.get("batch_size"),
                )
            )
        )
        if result.get("errors"):
            for error in result["errors"]:
                self.stdout.write(self.style.WARNING(str(error)))
