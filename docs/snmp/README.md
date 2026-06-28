# Production SNMP Worker

This backend includes a DB-driven SNMP polling worker for DCIM telemetry collection.

## Design

The SNMP worker is intentionally separated from request/response APIs:

- Django API manages inventory, credentials, OID mappings, telemetry, alerts, and RBAC.
- Celery Beat periodically enqueues due SNMP-enabled devices.
- Dedicated `snmp-worker` containers consume the `snmp` queue.
- Each poll reads active `SNMPOIDMapping` rows, writes `TelemetryPoint`, updates `LatestTelemetry`, evaluates alerts, updates device health, and creates `TelemetryIngestLog` records.

## Supported SNMP versions

- SNMP v1
- SNMP v2c
- SNMP v3 with auth/privacy

Supported SNMP v3 auth protocols:

- MD5
- SHA / SHA1
- SHA224
- SHA256
- SHA384
- SHA512

Supported privacy protocols:

- DES
- AES / AES128
- AES192
- AES256

## Production secret handling

Set a stable `FIELD_ENCRYPTION_KEY` in `.env` before saving SNMP credentials.
If this key changes, previously encrypted device secrets cannot be decrypted.

## Configure a device for SNMP v2c

```bash
python manage.py set_device_snmp <device-uuid> \
  --host 10.10.10.10 \
  --version V2C \
  --community 'public' \
  --interval 60 \
  --timeout 5 \
  --retries 2
```

## Configure a device for SNMP v3

```bash
python manage.py set_device_snmp <device-uuid> \
  --host 10.10.10.10 \
  --version V3 \
  --username snmpuser \
  --auth-protocol SHA256 \
  --auth-key 'strong-auth-key' \
  --priv-protocol AES128 \
  --priv-key 'strong-privacy-key' \
  --interval 60
```

## Create OID mappings

OID mappings are stored in `SNMPOIDMapping` and map device type/model to a metric.
For example, system uptime:

```text
metric_code: snmp_sys_uptime
oid: 1.3.6.1.2.1.1.3.0
data_type: integer
scale_factor: 1
```

## Run one manual poll

```bash
python manage.py snmp_poll_once <device-uuid>
```

With Docker:

```bash
docker compose exec api python manage.py snmp_poll_once <device-uuid>
```

## Automatic polling

`celery beat` runs `enqueue_due_snmp_polls` every `SNMP_ENQUEUE_INTERVAL_SECONDS` seconds.
The dedicated `snmp-worker` consumes queued device poll tasks.

Development:

```bash
docker compose up -d --build
```

Production:

```bash
docker compose -f docker-compose.production.yml up -d --build
```

## Operational notes

- SNMP workers should run inside the bank network or over a secure VPN that can reach device management IPs.
- Prefer SNMP v3 for banking environments.
- Use SNMP v2c only for devices that do not support v3.
- Do not expose SNMP ports to public networks.
- Keep polling intervals realistic. For hundreds of devices, use 30-120 seconds depending on criticality.
- Scale `snmp-worker` horizontally by adding replicas/containers.
- Monitor `TelemetryIngestLog` and Celery failures.
