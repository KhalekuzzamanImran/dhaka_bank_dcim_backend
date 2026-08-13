from __future__ import annotations

import os

from django.db import migrations, transaction


NEW_DEFINITION_NAME = "Daily alert summary"
NEW_DEFINITION_DESCRIPTION = "Exports detailed alert events for the day using the alert export column layout."
NEW_TEMPLATE_NAME = "Daily alert summary"
NEW_TEMPLATE_DESCRIPTION = "Detailed alert events for the selected day."
NEW_TEMPLATE_CONFIG = {
    "report_type": "alert_summary",
    "output_format": "csv",
    "allowed_output_formats": ["csv"],
    "required_filters": [],
    "optional_filters": [
        "date_from",
        "date_to",
        "data_center_id",
        "device_id",
        "device_model_id",
        "device_type_id",
        "room_id",
        "rack_id",
        "metric_codes",
        "severity",
        "status",
    ],
    "default_columns": [
        "triggered_at",
        "resolved_at",
        "organization",
        "data_center",
        "room",
        "rack",
        "device",
        "device_model",
        "metric",
        "severity",
        "status",
        "message",
        "occurrence_count",
        "acknowledged_by",
        "resolved_by",
    ],
    "max_date_range_days": 90,
}


def _sample_data_enabled() -> bool:
    from django.conf import settings

    return bool(settings.DEBUG or os.environ.get("REPORTS_SEED_SAMPLE_DATA"))


def forward(apps, schema_editor):
    if not _sample_data_enabled():
        return

    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")
    ReportJob = apps.get_model("reports", "ReportJob")
    ReportArtifact = apps.get_model("reports", "ReportArtifact")

    ReportDefinition.objects.filter(code="ALERT_SUMMARY").update(
        name=NEW_DEFINITION_NAME,
        description=NEW_DEFINITION_DESCRIPTION,
    )
    ReportTemplate.objects.filter(code="ALERT_SUMMARY").update(
        name=NEW_TEMPLATE_NAME,
        description=NEW_TEMPLATE_DESCRIPTION,
        config=NEW_TEMPLATE_CONFIG,
    )

    from apps.reports.models import ReportJobStatus
    from apps.reports.services.execution import generate_report_job

    latest_job = (
        ReportJob.objects.filter(
            template__code="ALERT_SUMMARY",
            status=ReportJobStatus.COMPLETED,
        )
        .order_by("-completed_at", "-created_at")
        .first()
    )
    if not latest_job:
        return

    with transaction.atomic():
        job = ReportJob.objects.select_for_update().get(pk=latest_job.pk)
        for artifact in list(job.artifacts.all()):
            file_name = artifact.file.name if artifact.file else ""
            if file_name:
                try:
                    artifact.file.storage.delete(file_name)
                except Exception:
                    pass
            artifact.delete()
        if job.file:
            file_name = job.file.name
            try:
                job.file.storage.delete(file_name)
            except Exception:
                pass
            job.file = None
            job.save(update_fields=["file", "updated_at"])

    generate_report_job(job.pk, queue_deliveries=False)


def backward(apps, schema_editor):
    if not _sample_data_enabled():
        return

    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    ReportDefinition.objects.filter(code="ALERT_SUMMARY").update(
        name="Daily alert summary",
        description="Summarises alerts by severity, status, and common operational filters for day-by-day review.",
    )
    ReportTemplate.objects.filter(code="ALERT_SUMMARY").update(
        name="Daily alert summary",
        description="Summary of alert activity, severity breakdown, and resolution counts for the day.",
        config={
            "report_type": "alert_summary",
            "output_format": "csv",
            "allowed_output_formats": ["csv"],
            "required_filters": [],
            "optional_filters": [
                "date_from",
                "date_to",
                "data_center_id",
                "device_id",
                "device_model_id",
                "device_type_id",
                "room_id",
                "rack_id",
                "metric_codes",
                "severity",
                "status",
            ],
            "default_columns": ["section", "label", "value"],
            "max_date_range_days": 90,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0027_alter_reportschedule_report_type"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
