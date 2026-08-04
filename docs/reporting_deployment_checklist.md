# Reporting Deployment Checklist

Use this checklist for production rollout of the normalized reporting platform.

## Before Deployment

- Verify database backup completion and restoreability.
- Confirm `python manage.py migrate` completes successfully in a fresh environment.
- Confirm `python manage.py check` passes.
- Confirm `python manage.py test --noinput apps.reports` passes.
- Confirm the frontend build passes.
- Confirm reporting logs are clean of tracebacks and database errors.
- Verify Celery workers for `reports`, `scheduler`, and notification queues are healthy.

## Deployment Order

1. Deploy backend code.
2. Run database migrations.
3. Restart API service.
4. Restart Celery workers.
5. Restart Celery beat.
6. Deploy frontend assets.

## Post-Deployment Validation

- Open the Reports dashboard.
- Generate a manual report.
- Run an existing schedule now.
- Confirm artifact download works.
- Confirm delivery rows transition through `QUEUED`, `DELIVERING`, and `SENT` or `FAILED`.
- Confirm dashboard aggregates render.
- Confirm retention cleanup dry-run works.

## Rollback

- Roll back application containers to the previous image.
- Do not revert historical migrations.
- Restore the verified database backup if a schema-level issue is discovered.
- Confirm the worker queues are drained or paused before rollback.

## Observability Checks

- Verify report generation logs include job, schedule, template, definition, organization, data-center, trigger source, and execution timing.
- Verify artifact downloads log the artifact and request context.
- Verify delivery retries and failures are visible in logs.

## Load Test Preparation

Prepared scenarios for later execution:

- 100 concurrent report jobs.
- Burst scheduling load.
- Large CSV/XLSX/PDF artifact generation.
- Concurrent artifact downloads.
- Delivery queue spikes.
- Dashboard concurrent reads.

Do not execute the load test until the production deployment is stable.
