from django.db import migrations


ADD_TEMPLATE_COLUMN_SQL = """
ALTER TABLE "report_schedules"
    ADD COLUMN IF NOT EXISTS "template_id" uuid NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'report_schedules_template_id_fk_report_templates'
          AND conrelid = 'report_schedules'::regclass
    ) THEN
        ALTER TABLE "report_schedules"
            ADD CONSTRAINT "report_schedules_template_id_fk_report_templates"
            FOREIGN KEY ("template_id")
            REFERENCES "report_templates" ("id")
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS "report_sche_templat_75e449_idx"
    ON "report_schedules" ("template_id");
"""


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0034_enable_all_report_formats_for_definitions_and_templates"),
    ]

    operations = [
        migrations.RunSQL(ADD_TEMPLATE_COLUMN_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
