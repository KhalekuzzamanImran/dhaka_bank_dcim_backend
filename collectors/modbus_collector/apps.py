from django.apps import AppConfig


class ModbusWorkerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "collectors.modbus_collector"
    verbose_name = "Modbus Worker"
