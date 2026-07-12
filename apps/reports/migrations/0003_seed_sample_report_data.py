from __future__ import annotations

import os
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import migrations
from django.utils import timezone


def seed_sample_report_data(apps, schema_editor):
    if not settings.DEBUG and not os.environ.get("REPORTS_SEED_SAMPLE_DATA"):
        return

    Organization = apps.get_model("organizations", "Organization")
    DataCenter = apps.get_model("datacenters", "DataCenter")
    User = apps.get_model("accounts", "User")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")
    ReportJob = apps.get_model("reports", "ReportJob")

    organization = Organization.objects.order_by("created_at").first()
    data_center = DataCenter.objects.filter(organization=organization).order_by("created_at").first() if organization else None
    user = User.objects.filter(is_active=True).order_by("date_joined").first()

    if not organization:
        return

    templates = [
        {
            "name": "Dummy Report Template",
            "code": "DUMMY_REPORT_TEMPLATE",
            "description": "Seeded sample report template used to verify the frontend report page.",
            "config": {"report_type": "alert_summary", "output_format": "csv"},
        },
        {
            "name": "Alert Summary",
            "code": "ALERT_SUMMARY",
            "description": "Summary of alert activity, severity breakdown, and resolution counts.",
            "config": {"report_type": "alert_summary", "output_format": "csv"},
        },
        {
            "name": "Notification Delivery",
            "code": "NOTIFICATION_DELIVERY",
            "description": "Notification delivery status grouped by channel and lifecycle state.",
            "config": {"report_type": "notification_delivery", "output_format": "csv"},
        },
        {
            "name": "Device Inventory",
            "code": "DEVICE_INVENTORY",
            "description": "Active device inventory scoped to the organization and data center.",
            "config": {"report_type": "device_inventory", "output_format": "csv"},
        },
    ]

    created_templates = {}
    for template_data in templates:
        template, _ = ReportTemplate.objects.update_or_create(
            organization=organization,
            code=template_data["code"],
            defaults={
                "name": template_data["name"],
                "description": template_data["description"],
                "config": template_data["config"],
                "is_active": True,
            },
        )
        created_templates[template_data["code"]] = template

    if not user:
        return

    def seed_job(template_code, *, status, days_ago, file_rows, error_message=""):
        template = created_templates.get(template_code)
        if not template:
            return

        exists = ReportJob.objects.filter(organization=organization, template=template, status=status).exists()
        if exists:
            return

        now = timezone.now() - timedelta(days=days_ago)
        started_at = now - timedelta(minutes=15)
        completed_at = now
        job = ReportJob.objects.create(
            organization=organization,
            data_center=data_center,
            template=template,
            requested_by=user,
            status=status,
            parameters={
                "report_type": template.config.get("report_type") if isinstance(template.config, dict) else None,
                "output_format": "csv",
            },
            started_at=started_at,
            completed_at=completed_at if status in {"COMPLETED", "FAILED", "CANCELLED"} else None,
            error_message=error_message,
        )

        if status == "COMPLETED":
            csv_content = "section,label,value\n" + "\n".join(
                f"{row['section']},{row['label']},{row['value']}" for row in file_rows
            )
            job.file.save(
                f"{template.code.lower()}-{job.id}.csv",
                ContentFile(csv_content.encode("utf-8")),
                save=True,
            )

        if status == "FAILED":
            job.error_message = error_message or "Sample report generation failed"
            job.save(update_fields=["error_message", "updated_at"])

    seed_job(
        "DUMMY_REPORT_TEMPLATE",
        status="PENDING",
        days_ago=0,
        file_rows=[],
    )
    seed_job(
        "ALERT_SUMMARY",
        status="COMPLETED",
        days_ago=0,
        file_rows=[
            {"section": "summary", "label": "open_total", "value": 3},
            {"section": "summary", "label": "resolved_total", "value": 12},
        ],
    )
    seed_job(
        "NOTIFICATION_DELIVERY",
        status="COMPLETED",
        days_ago=1,
        file_rows=[
            {"section": "summary", "label": "total", "value": 16},
            {"section": "summary", "label": "sent", "value": 14},
        ],
    )
    seed_job(
        "DEVICE_INVENTORY",
        status="FAILED",
        days_ago=2,
        file_rows=[],
        error_message="Sample report generation failed",
    )


def unseed_sample_report_data(apps, schema_editor):
    if not settings.DEBUG and not os.environ.get("REPORTS_SEED_SAMPLE_DATA"):
        return

    ReportJob = apps.get_model("reports", "ReportJob")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    ReportJob.objects.filter(template__code__in=["DUMMY_REPORT_TEMPLATE", "ALERT_SUMMARY", "NOTIFICATION_DELIVERY", "DEVICE_INVENTORY"]).delete()
    ReportTemplate.objects.filter(code__in=["DUMMY_REPORT_TEMPLATE", "ALERT_SUMMARY", "NOTIFICATION_DELIVERY", "DEVICE_INVENTORY"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0002_alter_reportjob_status"),
    ]

    operations = [
        migrations.RunPython(seed_sample_report_data, unseed_sample_report_data),
    ]
