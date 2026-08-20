from django.db import migrations


AGGREGATE_SQL = {
    "telemetry_5m": """
        CREATE MATERIALIZED VIEW telemetry_5m
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
        FROM telemetry_points
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
    "telemetry_1h": """
        CREATE MATERIALIZED VIEW telemetry_1h
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
        FROM telemetry_points
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
    "telemetry_1d": """
        CREATE MATERIALIZED VIEW telemetry_1d
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
        FROM telemetry_points
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
}

AGGREGATE_POLICIES = {
    "telemetry_5m": "SELECT add_continuous_aggregate_policy('telemetry_5m', start_offset => INTERVAL '2 days', end_offset => INTERVAL '5 minutes', schedule_interval => INTERVAL '5 minutes')",
    "telemetry_1h": "SELECT add_continuous_aggregate_policy('telemetry_1h', start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '1 hour')",
    "telemetry_1d": "SELECT add_continuous_aggregate_policy('telemetry_1d', start_offset => INTERVAL '730 days', end_offset => INTERVAL '1 day', schedule_interval => INTERVAL '1 day')",
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
        for name in ("telemetry_1d", "telemetry_1h", "telemetry_5m"):
            try:
                cursor.execute(f"SELECT remove_continuous_aggregate_policy('{name}', if_exists => TRUE)")
            except Exception:
                pass
            try:
                cursor.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
            except Exception:
                pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("telemetry", "0003_timescaledb_telemetry_points"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
