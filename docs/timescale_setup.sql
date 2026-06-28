CREATE EXTENSION IF NOT EXISTS timescaledb;
SELECT create_hypertable('telemetry_points', 'time', if_not_exists => TRUE);
ALTER TABLE telemetry_points SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'device_id, metric_id'
);
SELECT add_compression_policy('telemetry_points', INTERVAL '7 days');
-- Adjust retention according to bank policy.
-- SELECT add_retention_policy('telemetry_points', INTERVAL '365 days');
