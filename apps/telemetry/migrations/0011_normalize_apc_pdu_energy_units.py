from django.db import migrations


def normalize_energy_units(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE snmp_oid_mappings AS mapping
            SET scale_factor = 0.1
            FROM metric_definitions AS metric
            WHERE mapping.metric_id = metric.id
              AND metric.code IN ('pdu_total_energy', 'pdu_group_total_energy')
            """
        )
        cursor.execute(
            """
            UPDATE telemetry_points AS point
            SET value_float = point.value_float / 10.0
            FROM metric_definitions AS metric
            WHERE point.metric_id = metric.id
              AND metric.code IN ('pdu_total_energy', 'pdu_group_total_energy')
              AND point.value_float IS NOT NULL
            """
        )
        cursor.execute(
            """
            UPDATE latest_telemetry AS latest
            SET value_float = latest.value_float / 10.0
            FROM metric_definitions AS metric
            WHERE latest.metric_id = metric.id
              AND metric.code IN ('pdu_total_energy', 'pdu_group_total_energy')
              AND latest.value_float IS NOT NULL
            """
        )


def reverse_energy_units(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE snmp_oid_mappings AS mapping
            SET scale_factor = 1
            FROM metric_definitions AS metric
            WHERE mapping.metric_id = metric.id
              AND metric.code IN ('pdu_total_energy', 'pdu_group_total_energy')
            """
        )
        cursor.execute(
            """
            UPDATE telemetry_points AS point
            SET value_float = point.value_float * 10.0
            FROM metric_definitions AS metric
            WHERE point.metric_id = metric.id
              AND metric.code IN ('pdu_total_energy', 'pdu_group_total_energy')
              AND point.value_float IS NOT NULL
            """
        )
        cursor.execute(
            """
            UPDATE latest_telemetry AS latest
            SET value_float = latest.value_float * 10.0
            FROM metric_definitions AS metric
            WHERE latest.metric_id = metric.id
              AND metric.code IN ('pdu_total_energy', 'pdu_group_total_energy')
              AND latest.value_float IS NOT NULL
            """
        )


class Migration(migrations.Migration):
    dependencies = [("telemetry", "0010_compress_continuous_aggregates")]

    operations = [
        migrations.RunPython(normalize_energy_units, reverse_energy_units),
    ]
