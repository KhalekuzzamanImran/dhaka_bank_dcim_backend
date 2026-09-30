# Query performance diagnostics

The backend has opt-in request-level database monitoring through
`QueryMonitoringMiddleware`.

Set the following environment variables during a controlled investigation:

```text
QUERY_MONITORING_ENABLED=true
QUERY_MONITORING_SLOW_MS=100
QUERY_MONITORING_EXPLAIN_ENABLED=true
QUERY_MONITORING_EXPLAIN_SLOW_MS=250
QUERY_MONITORING_EXPLAIN_PATHS=/api/v1/devices/devices/
```

The API response includes `X-DB-Query-Count`, `X-DB-Query-Time-Ms`, and
`X-Request-Time-Ms`. Slow queries and `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`
plans are written to the `dcim.query_monitoring` logger.

`EXPLAIN ANALYZE` executes a read query a second time. Keep it disabled during
normal production traffic and enable it only for a short, controlled test.

Raw telemetry and canonical aggregates retain the most recent 12 months. The
obsolete `telemetry_points_timescale` mirror and its legacy aggregates are
removed by migration `0007`.

Effective access scopes are cached briefly and invalidated when access rows or
roles change. Keep the cache backend on Redis in production; local-memory
caching does not share scope results between API workers.

For fast device-page loading, request device metadata first and load activity
only when needed. Existing clients retain the old response by default; clients
may use `?include_activity=false` on the device detail endpoint and call the
`activity/` endpoint separately with explicit `active_limit` and `recent_limit`.

Telemetry API ingestion and collector writes use batched inserts and
PostgreSQL conflict upserts. The alert activity path uses a denormalized
`device_id` index, while retaining a compatibility fallback for legacy log
rows.

The API deployment uses Gunicorn with Uvicorn workers so HTTP and WebSocket
traffic can use multiple processes. Set `API_WORKERS` according to available
CPU and WebSocket load, then rebuild the image before deployment.
