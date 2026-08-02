from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0008_rename_report_sch_org_dc_idx_report_sche_organiz_4e6c26_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportScheduleRun",
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
                ("window_start", models.DateTimeField()),
                ("window_end", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("PROCESSING", "Processing"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="PENDING",
                        max_length=30,
                    ),
                ),
                ("queued_at", models.DateTimeField(default=timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("trigger_source", models.CharField(default="SCHEDULED", max_length=32)),
                ("snapshot", models.JSONField(blank=True, default=dict)),
                (
                    "generated_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="reports.reportjob",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_schedule_runs",
                        to="organizations.organization",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="requested_report_schedule_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "schedule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runs",
                        to="reports.reportschedule",
                    ),
                ),
            ],
            options={
                "db_table": "report_schedule_runs",
                "indexes": [
                    models.Index(fields=["schedule"], name="report_sch_run_sched_idx"),
                    models.Index(fields=["status"], name="report_sch_run_stat_idx"),
                    models.Index(fields=["queued_at"], name="report_sch_run_queued_idx"),
                    models.Index(fields=["started_at"], name="report_sch_run_start_idx"),
                    models.Index(fields=["completed_at"], name="report_sch_run_comp_idx"),
                    models.Index(fields=["created_at"], name="report_sch_run_crea_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ReportScheduleDelivery",
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
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("EMAIL", "Email"),
                            ("SMS", "SMS"),
                            ("WEB", "Web"),
                            ("WEBHOOK", "Webhook"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("DELIVERING", "Delivering"),
                            ("SENT", "Sent"),
                            ("FAILED", "Failed"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=30,
                    ),
                ),
                ("recipient_address", models.CharField(blank=True, default="", max_length=255)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=3)),
                ("queued_at", models.DateTimeField(blank=True, null=True)),
                ("delivering_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("failed_at", models.DateTimeField(blank=True, null=True)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("provider_message_id", models.CharField(blank=True, default="", max_length=255)),
                ("provider_response", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="reports.reportschedulerun",
                    ),
                ),
            ],
            options={
                "db_table": "report_schedule_deliveries",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["run", "channel", "recipient_address"],
                        name="unique_report_schedule_delivery_target",
                    ),
                ],
                "indexes": [
                    models.Index(fields=["run"], name="report_sch_del_run_idx"),
                    models.Index(fields=["channel"], name="report_sch_del_chan_idx"),
                    models.Index(fields=["status"], name="report_sch_del_stat_idx"),
                    models.Index(fields=["next_retry_at"], name="report_sch_del_next_idx"),
                    models.Index(fields=["created_at"], name="report_sch_del_crea_idx"),
                    models.Index(fields=["sent_at"], name="report_sch_del_sent_idx"),
                ],
            },
        ),
    ]
