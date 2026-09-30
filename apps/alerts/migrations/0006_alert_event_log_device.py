import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0005_detail_query_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="alerteventlog",
            name="device",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="alert_event_logs",
                to="devices.device",
            ),
        ),
        migrations.RunSQL(
            sql="""
                UPDATE alert_event_logs AS logs
                SET device_id = events.device_id
                FROM alert_events AS events
                WHERE logs.alert_event_id = events.id
                  AND logs.device_id IS NULL
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddIndex(
            model_name="alerteventlog",
            index=models.Index(fields=["device", "created_at"], name="alert_event_log_device_created"),
        ),
    ]
