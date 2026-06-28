from django.core.management.base import BaseCommand, CommandError

from collectors.snmp_collector.services import poll_snmp_device


class Command(BaseCommand):
    help = "Poll one SNMP-enabled device immediately and store telemetry."

    def add_arguments(self, parser):
        parser.add_argument("device_id", help="Device UUID")
        parser.add_argument("--no-alerts", action="store_true", help="Do not evaluate alert rules after latest telemetry updates")

    def handle(self, *args, **options):
        try:
            outcome = poll_snmp_device(options["device_id"], evaluate_alerts=not options["no_alerts"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        style = self.style.SUCCESS if outcome.status in {"SUCCESS", "PARTIAL_SUCCESS"} else self.style.ERROR
        self.stdout.write(style(f"{outcome.status}: device={outcome.device_id} ingest={outcome.ingest_id} success={outcome.success_count} failed={outcome.failure_count}"))
        if outcome.error_message:
            self.stdout.write(self.style.WARNING(outcome.error_message))
