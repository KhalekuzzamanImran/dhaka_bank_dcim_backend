from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0013_seed_definitions_and_backfill_structured_reporting"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="reportjob",
            constraint=models.CheckConstraint(
                check=models.Q(idempotency_key__isnull=True) | ~models.Q(idempotency_key=""),
                name="report_job_idempotency_key_not_blank",
            ),
        ),
    ]
