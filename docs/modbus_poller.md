# Modbus TCP Poller

Modbus polling logic lives in `collectors/modbus_collector/`.

- `services.py`: Modbus TCP polling workflow.
- `tasks.py`: Celery tasks.
- `exceptions.py`: Modbus errors.

The worker uses device-level locks and gateway-level locks to avoid duplicate polling and shared gateway contention.
