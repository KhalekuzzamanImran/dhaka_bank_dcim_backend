from __future__ import annotations

from django.db import migrations


NEW_DEFINITION_NAME = "Daily alert summary"
NEW_DEFINITION_DESCRIPTION = "Summarises alerts by severity, status, and common operational filters for the day."
OLD_DEFINITION_NAME = "Alert Summary"
OLD_DEFINITION_DESCRIPTION = "Summarises alerts by severity, status, and common operational filters."

NEW_TEMPLATE_NAME = "Daily alert summary"
NEW_TEMPLATE_DESCRIPTION = "Summary of alert activity, severity breakdown, and resolution counts for the day."
OLD_TEMPLATE_NAME = "Alert Summary"
OLD_TEMPLATE_DESCRIPTION = "Summary of alert activity, severity breakdown, and resolution counts."


def forward(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    ReportDefinition.objects.filter(code="ALERT_SUMMARY").update(
        name=NEW_DEFINITION_NAME,
        description=NEW_DEFINITION_DESCRIPTION,
    )
    ReportTemplate.objects.filter(code="ALERT_SUMMARY").update(
        name=NEW_TEMPLATE_NAME,
        description=NEW_TEMPLATE_DESCRIPTION,
    )


def backward(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    ReportDefinition.objects.filter(code="ALERT_SUMMARY").update(
        name=OLD_DEFINITION_NAME,
        description=OLD_DEFINITION_DESCRIPTION,
    )
    ReportTemplate.objects.filter(code="ALERT_SUMMARY").update(
        name=OLD_TEMPLATE_NAME,
        description=OLD_TEMPLATE_DESCRIPTION,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0024_reportschedulerecipient_destination_fields"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
