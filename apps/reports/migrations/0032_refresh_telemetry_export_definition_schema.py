from django.db import migrations


NEW_PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "device_id": {"type": ["string", "null"]},
        "metric_codes": {"type": "array", "items": {"type": "string"}},
        "default_columns": {"type": "array", "items": {"type": "string"}},
        "aggregation": {"type": ["string", "null"]},
        "quality_filter": {"type": ["string", "null"]},
    },
}

OLD_PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "aggregation": {"type": ["string", "null"]},
        "metric_codes": {"type": "array", "items": {"type": "string"}},
        "quality_filter": {"type": ["string", "null"]},
        "default_columns": {"type": "array", "items": {"type": "string"}},
    },
}


def forwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportDefinition.objects.filter(code="TELEMETRY_EXPORT").update(parameter_schema=NEW_PARAMETER_SCHEMA)


def backwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportDefinition.objects.filter(code="TELEMETRY_EXPORT").update(parameter_schema=OLD_PARAMETER_SCHEMA)


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0031_refresh_report_definition_seeds"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
