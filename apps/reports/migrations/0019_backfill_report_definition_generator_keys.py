from django.db import migrations


GENERATOR_KEY_MAP = {
    "DEVICE_INVENTORY": "device_inventory",
    "TELEMETRY_EXPORT": "telemetry_export",
    "ALERT_SUMMARY": "alert_summary",
    "ALERT_DETAIL": "alert_export",
    "NOTIFICATION_DELIVERY": "notification_delivery",
    "AUDIT_EXPORT": "audit_export",
    "ENVIRONMENTAL_TREND": "room_environment",
    "UPS_PERFORMANCE": "ups_performance",
}


def forwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for code, generator_key in GENERATOR_KEY_MAP.items():
        ReportDefinition.objects.filter(code=code).update(generator_key=generator_key)


def backwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for code in GENERATOR_KEY_MAP:
        ReportDefinition.objects.filter(code=code).update(generator_key=None)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0018_alter_reportartifact_file_alter_reportjob_file"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
