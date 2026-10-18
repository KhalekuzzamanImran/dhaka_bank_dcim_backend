from django.core.management.base import BaseCommand

from collectors.modbus_collector.reverse_gateway import main


class Command(BaseCommand):
    help = "Run the persistent listener for outbound-connected Modbus RTU gateways."

    def handle(self, *args, **options):
        main()
