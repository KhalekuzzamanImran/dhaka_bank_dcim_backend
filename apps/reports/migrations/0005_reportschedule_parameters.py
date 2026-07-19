from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0004_reportschedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="reportschedule",
            name="parameters",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
