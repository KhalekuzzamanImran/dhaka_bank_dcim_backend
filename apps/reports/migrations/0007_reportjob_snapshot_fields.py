from __future__ import annotations

from django.db import migrations, models
from django.utils import timezone


def _add_column_if_not_exists(table_name: str, column_name: str, column_sql: str) -> str:
    return f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{column_name}" {column_sql};'


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0006_reportschedule_sms"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "output_config_snapshot", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "parameters_snapshot", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "queued_at", "timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "retry_count", "integer NOT NULL DEFAULT 0"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "recipient_snapshot", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "scope_snapshot", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "template_config_snapshot", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
                ),
                migrations.RunSQL(
                    _add_column_if_not_exists("report_jobs", "trigger_source", "varchar(32) NOT NULL DEFAULT 'MANUAL'"),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="reportjob",
                    name="output_config_snapshot",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="parameters_snapshot",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="queued_at",
                    field=models.DateTimeField(default=timezone.now),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="retry_count",
                    field=models.PositiveIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="recipient_snapshot",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="scope_snapshot",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="template_config_snapshot",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="reportjob",
                    name="trigger_source",
                    field=models.CharField(default="MANUAL", max_length=32),
                ),
            ],
        ),
    ]
