from __future__ import annotations

from django.db import migrations, models


def _add_column_sql(table: str, column: str, definition: str) -> str:
    return f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = '{table}'
          AND column_name = '{column}'
    ) THEN
        EXECUTE 'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}';
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0010_rename_report_schedule_run_delivery_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(_add_column_sql("report_schedules", "attachment_formats", "jsonb NOT NULL DEFAULT ''[]''::jsonb")),
                migrations.RunSQL(_add_column_sql("report_schedules", "consecutive_failure_count", "integer NOT NULL DEFAULT 0")),
                migrations.RunSQL(_add_column_sql("report_schedules", "end_at", "timestamp with time zone NULL")),
                migrations.RunSQL(_add_column_sql("report_schedules", "last_success_at", "timestamp with time zone NULL")),
                migrations.RunSQL(_add_column_sql("report_schedules", "primary_format", "varchar(30) NULL")),
                migrations.RunSQL(_add_column_sql("report_schedules", "recurrence_rule", "jsonb NOT NULL DEFAULT ''{}''::jsonb")),
                migrations.RunSQL(_add_column_sql("report_schedules", "start_at", "timestamp with time zone NULL")),
                migrations.RunSQL(_add_column_sql("report_schedules", "status", "varchar(30) NOT NULL DEFAULT ''ACTIVE''")),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="reportschedule",
                    name="attachment_formats",
                    field=models.JSONField(blank=True, default=list),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="consecutive_failure_count",
                    field=models.PositiveIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="end_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="last_success_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="primary_format",
                    field=models.CharField(blank=True, max_length=30, null=True),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="recurrence_rule",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="start_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="reportschedule",
                    name="status",
                    field=models.CharField(default="ACTIVE", max_length=30),
                ),
            ],
        ),
    ]
