from django.db import migrations


GENERATOR_KEY_MAP = {
    "device_inventory": "device_inventory",
    "telemetry_export": "telemetry_export",
    "alert_summary": "alert_summary",
    "alert_export": "alert_export",
    "notification_delivery": "notification_delivery",
    "audit_export": "audit_export",
    "room_environment": "room_environment",
    "ups_performance": "ups_performance",
}


def forwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for code, generator_key in GENERATOR_KEY_MAP.items():
        ReportDefinition.objects.filter(code__iexact=code).update(generator_key=generator_key)


def backwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for code in GENERATOR_KEY_MAP:
        ReportDefinition.objects.filter(code__iexact=code).update(generator_key=None)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0019_backfill_report_definition_generator_keys"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
