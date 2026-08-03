from django.db import migrations


def forwards(apps, schema_editor):
    from apps.reports.definition_seeds import REPORT_DEFINITION_SEEDS
    from apps.reports.services.definitions import seed_report_definitions

    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    seed_report_definitions(ReportDefinitionModel=ReportDefinition, seeds=REPORT_DEFINITION_SEEDS)


def backwards(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0014_reportjob_idempotency_key_not_blank"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

