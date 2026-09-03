from django.db import migrations


def forwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")

    # Enable CSV, XLSX, PDF across all report definitions
    for defn in ReportDefinition.objects.all():
        defn.supported_formats = ["CSV", "XLSX", "PDF"]
        defn.save(update_fields=["supported_formats", "updated_at"])

    # Update all templates to allow csv, xlsx, pdf
    for template in ReportTemplate.objects.all():
        cfg = dict(template.config or {})
        cfg["allowed_output_formats"] = ["csv", "xlsx", "pdf"]
        template.config = cfg
        template.save(update_fields=["config", "updated_at"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0033_add_device_ids_to_telemetry_export_definition_schema"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
