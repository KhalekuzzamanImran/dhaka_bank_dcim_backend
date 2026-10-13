from copy import deepcopy

from django.db import migrations


DEFINITION_CODES = ("ALERT_SUMMARY", "ALERT_DETAIL")
DEVICE_ID_SCHEMA = {"type": ["string", "null"]}
DEVICE_IDS_SCHEMA = {"type": "array", "items": {"type": "string"}}


def forwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for definition in ReportDefinition.objects.filter(code__in=DEFINITION_CODES):
        schema = deepcopy(definition.parameter_schema or {})
        properties = schema.setdefault("properties", {})
        properties.setdefault("device_id", deepcopy(DEVICE_ID_SCHEMA))
        properties.setdefault("device_ids", deepcopy(DEVICE_IDS_SCHEMA))
        ReportDefinition.objects.filter(pk=definition.pk).update(parameter_schema=schema)


def backwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for definition in ReportDefinition.objects.filter(code__in=DEFINITION_CODES):
        schema = deepcopy(definition.parameter_schema or {})
        properties = schema.get("properties", {})
        properties.pop("device_id", None)
        properties.pop("device_ids", None)
        ReportDefinition.objects.filter(pk=definition.pk).update(parameter_schema=schema)


class Migration(migrations.Migration):
    dependencies = [("reports", "0035_reconcile_report_schedule_template")]

    operations = [migrations.RunPython(forwards, backwards)]
