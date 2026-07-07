# Bank DCIM Production Backend

Production-grade Django DCIM backend with a professional structure:

```text
bank_dcim_production_backend/
├── apps/                 # Business domains and API modules
├── collectors/           # SNMP, Modbus, scheduler, trap receiver workers
├── config/               # Django/DRF/Celery configuration
├── nginx/                # Nginx config
├── scripts/              # Operational scripts
├── deployment/           # Deployment assets
├── tests/                # Test skeletons
└── docs/                 # Architecture and operation docs
```

## Included Features

- Django + DRF backend
- JWT authentication
- Organization/data-center scoped RBAC
- DataCenter -> Room -> Row -> Rack -> Device hierarchy
- Device inventory and protocol configuration
- SNMP poller under `collectors/snmp_collector/`
- Modbus TCP poller under `collectors/modbus_collector/`
- SNMP Trap Receiver under `collectors/snmp_trap_receiver/`
- Polling scheduler under `collectors/scheduler/`
- Shared collector utilities under `collectors/common/`
- TimescaleDB telemetry model
- LatestTelemetry dashboard table
- Alerts, events, traps, reports, notifications, audit logs
- Celery queues for scheduler, SNMP, Modbus, traps, alerts, reports
- Docker Compose production-style services

## Runtime Services

```text
api
scheduler-worker
snmp-worker-1
snmp-worker-2
modbus-worker-1
modbus-worker-2
trap-receiver
trap-processor-worker
alert-worker
report-worker
beat
redis
db
nginx
```

## Run

```bash
cp .env.example .env
docker compose up -d --build

docker compose exec api python manage.py makemigrations
docker compose exec api python manage.py migrate
docker compose exec api python manage.py seed_dcim
```

Admin:

```text
http://localhost:8000/admin/
username: admin
password: admin12345
```

API Docs:

```text
http://localhost:8000/api/docs/
```

## Manual Operations

SNMP poll once:

```bash
docker compose exec api python manage.py snmp_poll_once <device-uuid>
```

Modbus poll once:

```bash
docker compose exec api python manage.py modbus_poll_once <device-uuid>
```

Process trap once:

```bash
docker compose exec api python manage.py process_trap_once 10.10.10.10 1.3.6.1.4.1.99999.1.1
```

## Notes

Before production use, replace seed OIDs/registers with real vendor maps, configure network/firewall rules, set a stable encryption key, and test device-specific SNMP/Modbus behavior.

## Notification and alert hardening

- Alert creation, update, resolve, and trap-confirmed PAC handling go through the central alert engine.
- Notification rows are created first, then Celery sends them asynchronously.
- Email and SMS support console/testing backends by default.
- Old `PENDING` notifications can be requeued with:

```bash
docker compose exec api python manage.py retry_pending_notifications --older-than-minutes 5 --limit 100
```

## Production hardening: running Celery workers as non-root

Celery currently runs as root in the default Compose setup, which is acceptable for local testing but not recommended for production.

The Docker image now creates a non-root `appuser` and `appgroup` so you can run services under a less privileged account in a production override.

Recommended production pattern:

```yaml
services:
  api:
    user: "10001:10001"
  alert-worker:
    user: "10001:10001"
  notification-worker:
    user: "10001:10001"
  beat:
    user: "10001:10001"
```

Make sure the following paths are writable by that user if you enable it:

- `/app/staticfiles`
- `/app/media`
- any log directory you mount

After changing the runtime user, restart the containers:

```bash
docker compose restart api beat alert-worker notification-worker
```


## Troubleshooting

### PySNMP import error

If workers fail with `ImportError: cannot import name CommunityData from pysnmp.hlapi`, rebuild the Docker images after this package fix:

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

This project uses the classic synchronous PySNMP HLAPI and pins:

```text
pysnmp==4.4.12
pyasn1==0.4.8
pycryptodomex==3.20.0
```

Do not change it to `pysnmp>=6` unless you also rewrite the SNMP client for the newer asyncio API.

### Database host error

If `migrate` fails with `[Errno -2] Name or service not known`, check the API container environment:

```bash
docker compose exec api env | grep DATABASE_URL
```

For Docker Compose it should be:

```text
DATABASE_URL=postgresql://dcim:dcim@db:5432/dcim
```

Then restart cleanly:

```bash
docker compose down -v
docker compose up -d --build
docker compose ps
docker compose exec api python manage.py migrate
```

## Correct startup order for first run

This package includes committed Django migrations. On a new database, run the schema migration before seeding data:

```bash
docker compose down -v
docker compose up -d --build

docker compose exec api python manage.py migrate
docker compose exec api python manage.py seed_dcim
```

If Beat starts before migrations are applied, the scheduler now skips polling until the database schema exists. After `migrate`, the next scheduler tick will run normally.

To avoid early scheduler warnings entirely, you can start only the infrastructure and API first:

```bash
docker compose down -v
docker compose up -d db redis api
docker compose exec api python manage.py migrate
docker compose exec api python manage.py seed_dcim
docker compose up -d
```
