from django.db import migrations


RECIPIENT_COLUMNS_SQL = """
ALTER TABLE "report_schedule_recipients"
    ADD COLUMN IF NOT EXISTS "recipient_type" varchar(30) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "destination" varchar(255) NOT NULL DEFAULT '';
ALTER TABLE "report_deliveries"
    ADD COLUMN IF NOT EXISTS "recipient_type" varchar(30) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "destination_snapshot" varchar(255) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "attempt_number" integer NOT NULL DEFAULT 0;
"""


class Migration(migrations.Migration):
    """Repair databases where 0024 recorded state but did not alter SQL schema."""

    dependencies = [
        ("reports", "0025_rename_alert_summary_labels"),
    ]

    operations = [
        migrations.RunSQL(RECIPIENT_COLUMNS_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
