# collectors.snmp_collector

Production SNMP polling app for the DCIM backend.

Main files:

- `client.py` - SNMP v1/v2c/v3 client wrapper using pysnmp.
- `services.py` - polling orchestration, telemetry persistence, health update, alert evaluation.
- `tasks.py` - Celery tasks for scheduled polling.
- `security.py` - Fernet-based field encryption helpers.
- `management/commands/set_device_snmp.py` - safely configure SNMP credentials.
- `management/commands/snmp_poll_once.py` - manually poll one device.
