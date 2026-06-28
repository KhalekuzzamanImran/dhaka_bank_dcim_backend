from django.apps import AppConfig


class SnmpTrapReceiverConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "collectors.snmp_trap_receiver"
    verbose_name = "SNMP Trap Receiver"
