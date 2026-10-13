from copy import deepcopy

from django.db import migrations


OLD_BANNER = "Dhaka Bank DCIM"
NEW_BANNER = "One Bank DCIM"
OLD_FOOTERS = {
    "Confidential — Dhaka Bank": "Confidential — One Bank",
    "Confidential – Dhaka Bank": "Confidential – One Bank",
    "Confidential - Dhaka Bank": "Confidential - One Bank",
    "Confidential - Dhaka Bank DCIM report": "Confidential - One Bank DCIM report",
}


def forwards(apps, schema_editor):
    ReportTemplate = apps.get_model("reports", "ReportTemplate")
    for template in ReportTemplate.objects.all().only("id", "config").iterator():
        config = deepcopy(template.config or {})
        header = config.get("report_header")
        if isinstance(header, dict):
            banner = header.get("brand_banner")
            if isinstance(banner, dict) and banner.get("label") == OLD_BANNER:
                banner["label"] = NEW_BANNER
        footer = config.get("report_footer")
        if isinstance(footer, dict) and footer.get("custom_text") in OLD_FOOTERS:
            footer["custom_text"] = OLD_FOOTERS[footer["custom_text"]]
        if config != (template.config or {}):
            ReportTemplate.objects.filter(pk=template.pk).update(config=config)


def backwards(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [("reports", "0036_add_device_scope_to_alert_definition_schemas")]

    operations = [migrations.RunPython(forwards, backwards)]
