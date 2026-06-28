from django.core.management.base import BaseCommand

from collectors.snmp_collector.tasks import enqueue_due_snmp_polls


class Command(BaseCommand):
    help = "Enqueue due SNMP polls using Celery."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        result = enqueue_due_snmp_polls.delay(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Submitted enqueue task {result.id}"))
