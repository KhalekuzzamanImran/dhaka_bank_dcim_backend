from __future__ import annotations

import uuid
from datetime import time

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0003_seed_sample_report_data"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportSchedule",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                (
                    "report_type",
                    models.CharField(
                        choices=[
                            ("alert_summary", "Alert Summary"),
                            ("notification_delivery", "Notification Delivery"),
                            ("device_inventory", "Device Inventory"),
                            ("room_environment", "Environmental Trends Report"),
                        ],
                        max_length=100,
                    ),
                ),
                (
                    "frequency",
                    models.CharField(
                        choices=[
                            ("DAILY", "Daily"),
                            ("WEEKLY", "Weekly"),
                            ("MONTHLY", "Monthly"),
                            ("QUARTERLY", "Quarterly"),
                        ],
                        default="DAILY",
                        max_length=30,
                    ),
                ),
                ("delivery_time", models.TimeField(default=time(6, 0))),
                (
                    "output_format",
                    models.CharField(
                        choices=[
                            ("CSV", "CSV"),
                            ("PDF", "PDF"),
                            ("PDF_CSV", "PDF / CSV"),
                        ],
                        default="PDF_CSV",
                        max_length=30,
                    ),
                ),
                ("recipients", models.JSONField(blank=True, default=list)),
                ("attach_raw_data", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
                ("next_run_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_delivery_status", models.CharField(blank=True, default="PENDING", max_length=30, null=True)),
                ("last_error_message", models.TextField(blank=True, null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_schedules",
                        to="organizations.organization",
                    ),
                ),
                (
                    "data_center",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_schedules",
                        to="datacenters.datacenter",
                    ),
                ),
                (
                    "last_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="reports.reportjob",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_report_schedules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "report_schedules",
                "indexes": [
                    models.Index(fields=["organization", "data_center"], name="report_sch_org_dc_idx"),
                    models.Index(fields=["report_type"], name="report_sch_type_idx"),
                    models.Index(fields=["frequency"], name="report_sch_freq_idx"),
                    models.Index(fields=["is_active"], name="report_sch_active_idx"),
                    models.Index(fields=["next_run_at"], name="report_sch_next_run_idx"),
                    models.Index(fields=["created_at"], name="report_sch_created_idx"),
                ],
            },
        ),
    ]
