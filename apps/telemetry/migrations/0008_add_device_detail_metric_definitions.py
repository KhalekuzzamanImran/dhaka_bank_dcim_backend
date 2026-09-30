from django.db import migrations


DEVICE_DETAIL_METRICS = (
    {
        "code": "pdu_load_percent",
        "name": "PDU Load Percent",
        "category": "POWER",
        "data_type": "FLOAT",
        "unit": "%",
    },
    {
        "code": "netbotz_hardware_revision",
        "name": "NetBotz Hardware Revision",
        "category": "OTHER",
        "data_type": "TEXT",
        "unit": None,
    },
    {
        "code": "ups_input_current",
        "name": "UPS Input Current",
        "category": "POWER",
        "data_type": "FLOAT",
        "unit": "A",
    },
)


def add_device_detail_metrics(apps, schema_editor):
    metric_model = apps.get_model("telemetry", "MetricDefinition")
    for metric in DEVICE_DETAIL_METRICS:
        code = metric["code"]
        defaults = {key: value for key, value in metric.items() if key != "code"}
        metric_model.objects.update_or_create(code=code, defaults=defaults)


def preserve_device_detail_metrics(apps, schema_editor):
    # These definitions may already be referenced by telemetry rows.
    pass


class Migration(migrations.Migration):
    dependencies = [("telemetry", "0007_remove_legacy_mirror_and_retention")]

    operations = [
        migrations.RunPython(add_device_detail_metrics, preserve_device_detail_metrics),
    ]
