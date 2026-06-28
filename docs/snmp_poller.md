# SNMP Poller

SNMP polling logic lives in `collectors/snmp_collector/`.

- `client.py`: low-level pysnmp client.
- `services.py`: device polling workflow.
- `tasks.py`: Celery tasks.
- `security.py`: encryption/decryption helper for SNMP secrets.
- `management/commands/`: manual operational commands.

Polling is DB-driven using `DeviceProtocolConfig`, `DeviceCredential`, `SNMPOIDMapping`, and `DevicePollingConfig`.
