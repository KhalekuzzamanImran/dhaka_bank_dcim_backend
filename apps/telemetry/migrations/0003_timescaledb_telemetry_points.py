from django.db import migrations


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
            cursor.execute(
                """
                SELECT create_hypertable(
                    'telemetry_points',
                    'time',
                    if_not_exists => TRUE,
                    migrate_data => TRUE
                )
                """
            )
        except Exception:
            return

        try:
            cursor.execute(
                """
                ALTER TABLE telemetry_points SET (
                    timescaledb.compress = TRUE,
                    timescaledb.compress_segmentby = 'device_id, metric_id',
                    timescaledb.compress_orderby = 'time DESC'
                )
                """
            )
        except Exception:
            pass

        try:
            cursor.execute("SELECT add_compression_policy('telemetry_points', INTERVAL '30 days')")
        except Exception:
            pass


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute("SELECT remove_compression_policy('telemetry_points', if_exists => TRUE)")
        except Exception:
            pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("telemetry", "0002_latesttelemetry_raw_value_text_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
