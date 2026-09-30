from django.db import migrations


RETENTION_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        PERFORM remove_retention_policy('telemetry_points', if_exists => TRUE);
        PERFORM add_retention_policy('telemetry_points', drop_after => INTERVAL '90 days', if_not_exists => TRUE);

        PERFORM remove_retention_policy('telemetry_5m', if_exists => TRUE);
        PERFORM add_retention_policy('telemetry_5m', drop_after => INTERVAL '6 months', if_not_exists => TRUE);

        PERFORM remove_retention_policy('telemetry_1h', if_exists => TRUE);
        PERFORM add_retention_policy('telemetry_1h', drop_after => INTERVAL '1 year', if_not_exists => TRUE);

        PERFORM remove_retention_policy('telemetry_1d', if_exists => TRUE);
        PERFORM add_retention_policy('telemetry_1d', drop_after => INTERVAL '1 year', if_not_exists => TRUE);
    END IF;
EXCEPTION WHEN undefined_function OR undefined_table THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        PERFORM remove_compression_policy('telemetry_points', if_exists => TRUE);
        PERFORM add_compression_policy('telemetry_points', compress_after => INTERVAL '14 days', if_not_exists => TRUE);

        BEGIN
            PERFORM remove_compression_policy('telemetry_5m', if_exists => TRUE);
            PERFORM add_compression_policy('telemetry_5m', compress_after => INTERVAL '45 days', if_not_exists => TRUE);
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
        BEGIN
            PERFORM remove_compression_policy('telemetry_1h', if_exists => TRUE);
            PERFORM add_compression_policy('telemetry_1h', compress_after => INTERVAL '90 days', if_not_exists => TRUE);
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
        BEGIN
            PERFORM remove_compression_policy('telemetry_1d', if_exists => TRUE);
            PERFORM add_compression_policy('telemetry_1d', compress_after => INTERVAL '180 days', if_not_exists => TRUE);
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;
EXCEPTION WHEN undefined_function OR undefined_table THEN
    NULL;
END
$$;
"""


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("telemetry", "0008_add_device_detail_metric_definitions")]

    operations = [
        migrations.RunSQL(sql=RETENTION_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
