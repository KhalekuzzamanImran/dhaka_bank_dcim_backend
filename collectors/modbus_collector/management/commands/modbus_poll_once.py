from django.core.management.base import BaseCommand, CommandError
from collectors.modbus_collector.services import poll_modbus_device


class Command(BaseCommand):
    help = "Poll one Modbus TCP device immediately and store telemetry."

    def add_arguments(self, parser):
        parser.add_argument("device_id", help="Device UUID")
        parser.add_argument("--no-alerts", action="store_true")

    def handle(self, *args, **options):
        try:
            outcome = poll_modbus_device(options["device_id"], evaluate_alerts=not options["no_alerts"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(str(outcome.__dict__)))
