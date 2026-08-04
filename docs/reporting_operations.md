# Reporting Operations

This document describes the normalized reporting platform used by the DCIM backend.

## Architecture

The reporting flow is:

1. Report definitions define supported capabilities and parameter schemas.
2. Templates provide reusable configuration within organization and data-center scope.
3. Schedules create jobs automatically or via Run Now.
4. The central factory creates immutable `ReportJob` records.
5. The execution service generates CSV, XLSX, or PDF output.
6. Generated files are persisted as `ReportArtifact` rows.
7. `ReportDelivery` and notification deliveries handle email and SMS distribution.
8. Downloads use the secure artifact download service.
9. Dashboard aggregates are computed server-side.

## API Endpoints

Canonical API routes:

- `GET /api/v1/reports/definitions/`
- `GET /api/v1/reports/definitions/{code}/`
- `GET /api/v1/reports/definitions/{code}/parameter-schema/`
- `GET|POST|PATCH|DELETE /api/v1/reports/templates/`
- `POST /api/v1/reports/templates/{id}/generate/`
- `GET|POST|PATCH|DELETE /api/v1/reports/schedules/`
- `POST /api/v1/reports/schedules/{id}/run-now/`
- `POST /api/v1/reports/schedules/{id}/pause/`
- `POST /api/v1/reports/schedules/{id}/resume/`
- `GET /api/v1/reports/schedules/{id}/runs/`
- `GET /api/v1/reports/schedules/{id}/deliveries/`
- `GET /api/v1/reports/jobs/`
- `GET /api/v1/reports/jobs/{id}/`
- `GET /api/v1/reports/artifacts/`
- `GET /api/v1/reports/artifacts/{id}/`
- `GET /api/v1/reports/artifacts/{id}/download/`
- `GET /api/v1/reports/deliveries/`
- `GET /api/v1/reports/deliveries/{id}/`
- `GET /api/v1/reports/dashboard/`
- `GET /api/v1/reports/options/`

Compatibility routes still exist in source for the current transition period, but they are no longer part of the canonical runtime path.

## Generation Flow

Generation is asynchronous:

1. A request creates or reuses a `ReportJob`.
2. The Celery worker claims the job.
3. The generator registry resolves the generator by definition.
4. The generator builds a dataset and renders requested formats.
5. Each file is persisted as a `ReportArtifact`.
6. The job is marked completed only after artifact persistence succeeds.
7. Delivery tasks are queued after commit.

## Scheduling Flow

Schedules are evaluated in the scheduler worker.

1. Active schedules with a due `next_run_at` are claimed.
2. The schedule window is normalized to the configured timezone.
3. The factory creates a job and a schedule run.
4. The execution service generates artifacts.
5. Delivery rows are created from the immutable recipient snapshot.
6. Delivery execution is handled asynchronously by the notification layer.

## Artifact Lifecycle

Artifacts are immutable runtime outputs.

- Stored in Django storage.
- Identified by `job + format`.
- Downloaded through the secure artifact endpoint.
- Removed by retention cleanup after the configured expiration period.

## Delivery Lifecycle

Delivery rows represent one recipient and one channel.

States:

- `PENDING`
- `QUEUED`
- `DELIVERING`
- `SENT`
- `FAILED`
- `CANCELLED`

`NotificationDelivery` remains the provider execution layer. `ReportDelivery` is the reporting-domain source of truth.

## Dashboard Aggregation

Dashboard values are computed on the backend from live database aggregates. The frontend should not derive totals by downloading jobs or deliveries.

## Operational Troubleshooting

Common checks:

- `python manage.py check`
- `python manage.py test --noinput apps.reports`
- Review API, scheduler, and worker logs for `ERROR`, `Traceback`, `ProgrammingError`, `IntegrityError`, and `OperationalError`.
- Verify retention cleanup and artifact downloads after deployment.

If a report does not generate:

- Confirm the definition is active.
- Confirm the template and schedule scopes match the organization/data-center access.
- Confirm the worker queue is running.
- Confirm the generator supports the requested format.

If downloads fail:

- Confirm the artifact file exists in storage.
- Confirm the user has access to the organization and data center.
- Confirm the artifact has not expired.

## Migration History

The reporting schema was introduced incrementally and repaired to migrate cleanly from zero. Historical migrations are retained intact for auditability.

## Compatibility Layer

Legacy report tables, fields, and routes remain in source during the cleanup window. They should not be used by new runtime paths.

