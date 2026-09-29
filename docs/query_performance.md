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
