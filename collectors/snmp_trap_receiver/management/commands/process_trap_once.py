from django.core.management.base import BaseCommand
from collectors.snmp_trap_receiver.services import process_snmp_trap


class Command(BaseCommand):
    help = "Manually process one synthetic SNMP trap for testing."

    def add_arguments(self, parser):
        parser.add_argument("source_ip")
        parser.add_argument("trap_oid")

    def handle(self, *args, **options):
        event = process_snmp_trap(source_ip=options["source_ip"], trap_oid=options["trap_oid"], raw_varbinds={})
        self.stdout.write(self.style.SUCCESS(f"Processed trap event {event.id}"))
