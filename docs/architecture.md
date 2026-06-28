# Bank DCIM Architecture

This project follows a modular Django architecture:

- `apps/` contains business domains and API modules.
- `collectors/` contains protocol/data-collection workers.
- `config/` contains Django, DRF, Celery and settings configuration.
- `nginx/`, `deployment/`, `scripts/`, `docs/`, and `tests/` support production operations.

Runtime services are separated through Docker/Celery queues: API, scheduler, SNMP workers, Modbus workers, SNMP trap receiver, trap processor, alert worker, and report worker.
