from django.db import migrations, models


AGGREGATES = {
    "telemetry_5m": "5 minutes",
    "telemetry_1h": "1 hour",
    "telemetry_1d": "1 day",
}


def _create_aggregate(cursor, name, bucket):
    cursor.execute("SELECT to_regclass(%s)", [name])
    if cursor.fetchone()[0] is not None:
        return
    cursor.execute(
        f"""
        CREATE MATERIALIZED VIEW {name}
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '{bucket}', time) AS bucket,
            organization_id,
            data_center_id,
            device_id,
            metric_id,
            AVG(COALESCE(
                value_float,
                value_integer::double precision,
                CASE WHEN value_boolean IS TRUE THEN 1.0
                     WHEN value_boolean IS FALSE THEN 0.0
                     ELSE NULL END
            )) AS avg_value,
            MIN(COALESCE(
                value_float,
                value_integer::double precision,
                CASE WHEN value_boolean IS TRUE THEN 1.0
                     WHEN value_boolean IS FALSE THEN 0.0
                     ELSE NULL END
            )) AS min_value,
            MAX(COALESCE(
                value_float,
                value_integer::double precision,
                CASE WHEN value_boolean IS TRUE THEN 1.0
                     WHEN value_boolean IS FALSE THEN 0.0
                     ELSE NULL END
            )) AS max_value,
            MAX(time) AS last_observed_at,
            COUNT(*) AS sample_count
        FROM telemetry_points
        WHERE value_float IS NOT NULL
           OR value_integer IS NOT NULL
           OR value_boolean IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
        WITH NO DATA
        """
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS {name}_device_metric_bucket_idx "
        f"ON {name} (device_id, metric_id, bucket DESC)"
    )


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        if cursor.fetchone() is None:
            return

        cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

        # The mirror trigger duplicates every write and is no longer needed
        # once the application table becomes the canonical hypertable.
        cursor.execute("DROP TRIGGER IF EXISTS telemetry_points_timescale_insert ON telemetry_points")

        cursor.execute(
            """
            SELECT 1
            FROM timescaledb_information.hypertables
            WHERE hypertable_schema = 'public' AND hypertable_name = 'telemetry_points'
            """
        )
        if cursor.fetchone() is None:
            # Timescale requires every unique index on a hypertable to include
            # the time dimension. Telemetry is append-only, so retain an
            # application-level id/time uniqueness guarantee instead of the
            # incompatible id-only primary key.
            cursor.execute("ALTER TABLE telemetry_points DROP CONSTRAINT IF EXISTS telemetry_points_pkey")
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS telemetry_points_id_time_uq
                ON telemetry_points (id, time)
                """
            )
            cursor.execute(
                """
                SELECT create_hypertable(
                    'telemetry_points',
                    'time',
                    if_not_exists => TRUE,
                    migrate_data => TRUE,
                    chunk_time_interval => INTERVAL '1 day'
                )
                """
            )

        cursor.execute(
            """
            ALTER TABLE telemetry_points SET (
                timescaledb.compress = TRUE,
                timescaledb.compress_segmentby = 'device_id, metric_id',
                timescaledb.compress_orderby = 'time DESC'
            )
            """
        )
        try:
            cursor.execute("SELECT add_compression_policy('telemetry_points', INTERVAL '30 days')")
        except Exception:
            pass

        for name, bucket in AGGREGATES.items():
            _create_aggregate(cursor, name, bucket)
            try:
                schedule = {"telemetry_5m": "5 minutes", "telemetry_1h": "1 hour", "telemetry_1d": "1 day"}[name]
                start_offset = {"telemetry_5m": "2 days", "telemetry_1h": "30 days", "telemetry_1d": "730 days"}[name]
                end_offset = schedule
                cursor.execute(
                    f"SELECT add_continuous_aggregate_policy('{name}', "
                    f"start_offset => INTERVAL '{start_offset}', "
                    f"end_offset => INTERVAL '{end_offset}', "
                    f"schedule_interval => INTERVAL '{schedule}')"
                )
            except Exception:
                pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("telemetry", "0005_timescaledb_telemetry_points_timescale_mirror"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="deviceevent",
            index=models.Index(fields=["device", "occurred_at"], name="device_event_device_time_idx"),
        ),
    ]
