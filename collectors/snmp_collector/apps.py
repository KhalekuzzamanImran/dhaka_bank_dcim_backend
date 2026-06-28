from django.apps import AppConfig


class SnmpWorkerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "collectors.snmp_collector"
    verbose_name = "SNMP Worker"
