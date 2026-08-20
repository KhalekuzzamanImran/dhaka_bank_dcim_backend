from django.db import migrations


MIRROR_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS telemetry_points_timescale (
        time timestamptz NOT NULL,
        organization_id uuid NOT NULL,
        data_center_id uuid NOT NULL,
        device_id uuid NOT NULL,
        metric_id uuid NOT NULL,
        value_float double precision NULL,
        value_integer bigint NULL,
        value_boolean boolean NULL,
        value_text text NULL,
        raw_value_text text NULL,
        quality varchar(20) NOT NULL,
        source varchar(50) NULL,
        ingest_id uuid NULL,
        created_at timestamptz NOT NULL
    )
"""


MIRROR_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS telemetry_points_timescale_device_metric_time_idx
    ON telemetry_points_timescale (device_id, metric_id, time DESC)
"""


MIRROR_TRIGGER_FN_SQL = """
    CREATE OR REPLACE FUNCTION telemetry_points_timescale_sync()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        INSERT INTO telemetry_points_timescale (
            time,
            organization_id,
            data_center_id,
            device_id,
            metric_id,
            value_float,
            value_integer,
            value_boolean,
            value_text,
            raw_value_text,
            quality,
            source,
            ingest_id,
            created_at
        ) VALUES (
            NEW.time,
            NEW.organization_id,
            NEW.data_center_id,
            NEW.device_id,
            NEW.metric_id,
            NEW.value_float,
            NEW.value_integer,
            NEW.value_boolean,
            NEW.value_text,
            NEW.raw_value_text,
            NEW.quality,
            NEW.source,
            NEW.ingest_id,
            NEW.created_at
        );
        RETURN NEW;
    END;
    $$;
"""


MIRROR_TRIGGER_SQL = """
    DROP TRIGGER IF EXISTS telemetry_points_timescale_insert ON telemetry_points;
    CREATE TRIGGER telemetry_points_timescale_insert
    AFTER INSERT ON telemetry_points
    FOR EACH ROW
    EXECUTE FUNCTION telemetry_points_timescale_sync()
"""


AGGREGATE_SQL = {
    "telemetry_points_ts_5m": """
        CREATE MATERIALIZED VIEW telemetry_points_ts_5m
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '5 minutes', "time") AS bucket,
            organization_id,
            data_center_id,
            device_id,
            metric_id,
            AVG(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS avg_value,
            MIN(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS min_value,
            MAX(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS max_value,
            MAX("time") AS last_observed_at,
            COUNT(*) AS sample_count
        FROM telemetry_points_timescale
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
    "telemetry_points_ts_1h": """
        CREATE MATERIALIZED VIEW telemetry_points_ts_1h
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '1 hour', "time") AS bucket,
            organization_id,
            data_center_id,
            device_id,
            metric_id,
            AVG(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS avg_value,
            MIN(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS min_value,
            MAX(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS max_value,
            MAX("time") AS last_observed_at,
            COUNT(*) AS sample_count
        FROM telemetry_points_timescale
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
    "telemetry_points_ts_1d": """
        CREATE MATERIALIZED VIEW telemetry_points_ts_1d
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '1 day', "time") AS bucket,
            organization_id,
            data_center_id,
            device_id,
            metric_id,
            AVG(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS avg_value,
            MIN(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS min_value,
            MAX(
                COALESCE(
                    value_float,
                    value_integer::double precision,
                    CASE
                        WHEN value_boolean IS TRUE THEN 1.0
                        WHEN value_boolean IS FALSE THEN 0.0
                        ELSE NULL
                    END
                )
            ) AS max_value,
            MAX("time") AS last_observed_at,
            COUNT(*) AS sample_count
        FROM telemetry_points_timescale
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
}

AGGREGATE_POLICIES = {
    "telemetry_points_ts_5m": "SELECT add_continuous_aggregate_policy('telemetry_points_ts_5m', start_offset => INTERVAL '2 days', end_offset => INTERVAL '5 minutes', schedule_interval => INTERVAL '5 minutes')",
    "telemetry_points_ts_1h": "SELECT add_continuous_aggregate_policy('telemetry_points_ts_1h', start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '1 hour')",
    "telemetry_points_ts_1d": "SELECT add_continuous_aggregate_policy('telemetry_points_ts_1d', start_offset => INTERVAL '730 days', end_offset => INTERVAL '1 day', schedule_interval => INTERVAL '1 day')",
}


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        if cursor.fetchone() is None:
            return

        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        except Exception:
            return

        try:
            cursor.execute(MIRROR_TABLE_SQL)
            cursor.execute(MIRROR_INDEX_SQL)
        except Exception:
            return

        try:
            cursor.execute(
                "SELECT create_hypertable('telemetry_points_timescale', 'time', if_not_exists => TRUE, migrate_data => TRUE)"
            )
        except Exception:
            return

        try:
            cursor.execute(MIRROR_TRIGGER_FN_SQL)
            cursor.execute(MIRROR_TRIGGER_SQL)
        except Exception:
            pass

        try:
            cursor.execute(
                """
                INSERT INTO telemetry_points_timescale (
                    time, organization_id, data_center_id, device_id, metric_id,
                    value_float, value_integer, value_boolean, value_text, raw_value_text,
                    quality, source, ingest_id, created_at
                )
                SELECT
                    time, organization_id, data_center_id, device_id, metric_id,
                    value_float, value_integer, value_boolean, value_text, raw_value_text,
                    quality, source, ingest_id, created_at
                FROM telemetry_points
                """
            )
        except Exception:
            pass

        for name, sql in AGGREGATE_SQL.items():
            try:
                cursor.execute(sql)
            except Exception:
                continue

            policy_sql = AGGREGATE_POLICIES.get(name)
            if policy_sql:
                try:
                    cursor.execute(policy_sql)
                except Exception:
                    pass

            try:
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {name}_device_metric_bucket_idx ON {name} (device_id, metric_id, bucket DESC)"
                )
            except Exception:
                pass


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for name in ("telemetry_points_ts_1d", "telemetry_points_ts_1h", "telemetry_points_ts_5m"):
            try:
                cursor.execute(f"SELECT remove_continuous_aggregate_policy('{name}', if_exists => TRUE)")
            except Exception:
                pass
            try:
                cursor.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
            except Exception:
                pass

        try:
            cursor.execute("DROP TRIGGER IF EXISTS telemetry_points_timescale_insert ON telemetry_points")
        except Exception:
            pass
        try:
            cursor.execute("DROP FUNCTION IF EXISTS telemetry_points_timescale_sync()")
        except Exception:
            pass
        try:
            cursor.execute("DROP TABLE IF EXISTS telemetry_points_timescale")
        except Exception:
            pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("telemetry", "0004_timescaledb_telemetry_continuous_aggregates"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
