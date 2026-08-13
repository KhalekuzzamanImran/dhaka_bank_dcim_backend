from __future__ import annotations

from django.db import migrations, models


NEW_DEFINITION_NAME = "Alert Summary"
NEW_DEFINITION_DESCRIPTION = "Exports detailed alert events with acknowledgement and resolution information."
NEW_TEMPLATE_NAME = "Alert Summary"
NEW_TEMPLATE_DESCRIPTION = "Exports detailed alert history with device, metric, severity, status, acknowledgement, and resolution details."


def forward(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    ReportDefinition.objects.filter(code="ALERT_DETAIL").update(
        name=NEW_DEFINITION_NAME,
        description=NEW_DEFINITION_DESCRIPTION,
    )
    ReportTemplate.objects.filter(code="ALERT_EXPORT").update(
        name=NEW_TEMPLATE_NAME,
        description=NEW_TEMPLATE_DESCRIPTION,
    )


def backward(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    ReportDefinition.objects.filter(code="ALERT_DETAIL").update(
        name="Alert Detail",
        description="Exports detailed alert events with acknowledgement and resolution information.",
    )
    ReportTemplate.objects.filter(code="ALERT_EXPORT").update(
        name="Alert Export",
        description="Exports detailed alert history with device, metric, severity, status, acknowledgement, and resolution details.",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0029_rename_alert_detail_labels"),
    ]

    operations = [
        migrations.AlterField(
            model_name="reportschedule",
            name="report_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("alert_summary", "Daily alert summary"),
                    ("notification_delivery", "Notification Delivery"),
                    ("device_inventory", "Device Inventory"),
                    ("alert_export", "Alert Summary"),
                    ("audit_export", "Audit Export"),
                    ("telemetry_export", "Telemetry Export"),
                    ("room_environment", "Environmental Trends Report"),
                    ("ups_performance", "UPS Performance"),
                ],
                default="",
                max_length=100,
            ),
        ),
        migrations.RunPython(forward, backward),
    ]
