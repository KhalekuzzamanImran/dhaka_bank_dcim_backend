from django.db import migrations


DROP_LEGACY_SQL = """
DO $$
DECLARE
    aggregate_name text;
BEGIN
    FOREACH aggregate_name IN ARRAY ARRAY[
        'telemetry_points_ts_1d',
        'telemetry_points_ts_1h',
        'telemetry_points_ts_5m'
    ] LOOP
        PERFORM remove_continuous_aggregate_policy(aggregate_name, if_exists => TRUE);
        EXECUTE format('DROP MATERIALIZED VIEW IF EXISTS %I', aggregate_name);
    END LOOP;

    DROP TABLE IF EXISTS telemetry_points_timescale;
    DROP FUNCTION IF EXISTS telemetry_points_timescale_sync();
END
$$;
"""


RETENTION_SQL = """
SELECT remove_retention_policy('telemetry_points', if_exists => TRUE);
SELECT add_retention_policy(
    'telemetry_points',
    drop_after => INTERVAL '12 months',
    if_not_exists => TRUE
);

SELECT remove_retention_policy('telemetry_5m', if_exists => TRUE);
SELECT add_retention_policy(
    'telemetry_5m',
    drop_after => INTERVAL '12 months',
    if_not_exists => TRUE
);

SELECT remove_retention_policy('telemetry_1h', if_exists => TRUE);
SELECT add_retention_policy(
    'telemetry_1h',
    drop_after => INTERVAL '12 months',
    if_not_exists => TRUE
);

SELECT remove_retention_policy('telemetry_1d', if_exists => TRUE);
SELECT add_retention_policy(
    'telemetry_1d',
    drop_after => INTERVAL '12 months',
    if_not_exists => TRUE
);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("telemetry", "0006_canonical_timescale_and_detail_indexes"),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_LEGACY_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=RETENTION_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
