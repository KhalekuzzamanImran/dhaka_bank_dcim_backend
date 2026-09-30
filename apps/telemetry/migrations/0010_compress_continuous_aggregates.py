from django.db import migrations


SQL = """
ALTER MATERIALIZED VIEW telemetry_5m SET (timescaledb.compress = true);
ALTER MATERIALIZED VIEW telemetry_1h SET (timescaledb.compress = true);
ALTER MATERIALIZED VIEW telemetry_1d SET (timescaledb.compress = true);

SELECT remove_compression_policy('telemetry_5m', if_exists => TRUE);
SELECT add_compression_policy('telemetry_5m', compress_after => INTERVAL '45 days', if_not_exists => TRUE);

SELECT remove_compression_policy('telemetry_1h', if_exists => TRUE);
SELECT add_compression_policy('telemetry_1h', compress_after => INTERVAL '90 days', if_not_exists => TRUE);

SELECT remove_continuous_aggregate_policy('telemetry_1d', if_exists => TRUE);
SELECT add_continuous_aggregate_policy(
    'telemetry_1d',
    start_offset => INTERVAL '180 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);
SELECT remove_compression_policy('telemetry_1d', if_exists => TRUE);
SELECT add_compression_policy('telemetry_1d', compress_after => INTERVAL '180 days', if_not_exists => TRUE);
"""


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("telemetry", "0009_retention_policies")]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
