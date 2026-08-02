from django.db import migrations


def strip_schedule_timezone(apps, schema_editor):
    ReportSchedule = apps.get_model("reports", "ReportSchedule")
    db = schema_editor.connection.alias
    for schedule in ReportSchedule.objects.using(db).all().iterator():
        recurrence_rule = schedule.recurrence_rule if isinstance(schedule.recurrence_rule, dict) else {}
        if "timezone" not in recurrence_rule:
            continue
        recurrence_rule = dict(recurrence_rule)
        recurrence_rule.pop("timezone", None)
        ReportSchedule.objects.using(db).filter(pk=schedule.pk).update(recurrence_rule=recurrence_rule)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0009_alter_reportjob_status"),
    ]

    operations = [
        migrations.RunPython(strip_schedule_timezone, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="reportschedule",
            name="timezone",
        ),
    ]
