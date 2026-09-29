from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0004_rename_ac_event_idx_alert_comme_alert_e_3be377_idx_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="alertevent",
            index=models.Index(fields=["device", "status", "triggered_at"], name="alert_event_device_status_time"),
        ),
        migrations.AddIndex(
            model_name="alerteventlog",
            index=models.Index(fields=["alert_event", "created_at"], name="alert_event_log_event_created"),
        ),
    ]
