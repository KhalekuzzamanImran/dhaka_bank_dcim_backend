from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0005_reportschedule_parameters"),
    ]

    operations = [
        migrations.AddField(
            model_name="reportschedule",
            name="send_sms",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="reportschedule",
            name="sms_recipients",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
