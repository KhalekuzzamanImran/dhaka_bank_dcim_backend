from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.traps.services import import_snmp_mib_files


class Command(BaseCommand):
    help = "Import trusted SNMP MIB files into the local registry cache."

    def add_arguments(self, parser):
        parser.add_argument(
            "--directory",
            action="append",
            dest="directories",
            help="Trusted directory or MIB file to import. Can be supplied multiple times. Defaults to SNMP_MIB_DIRECTORIES.",
        )

    def handle(self, *args, **options):
        directories = options.get("directories") or list(getattr(settings, "SNMP_MIB_DIRECTORIES", []))
        if not directories:
            raise CommandError("No MIB directories configured.")

        summary = import_snmp_mib_files(directories)
        self.stdout.write(
            self.style.SUCCESS(
                "Imported SNMP MIBs: files_processed={files_processed} definitions_imported={definitions_imported} definitions_updated={definitions_updated} definitions_deactivated={definitions_deactivated} files_skipped={files_skipped} files_failed={files_failed}".format(
                    files_processed=summary.files_processed,
                    definitions_imported=summary.definitions_imported,
                    definitions_updated=summary.definitions_updated,
                    definitions_deactivated=summary.definitions_deactivated,
                    files_skipped=summary.files_skipped,
                    files_failed=summary.files_failed,
                )
            )
        )
