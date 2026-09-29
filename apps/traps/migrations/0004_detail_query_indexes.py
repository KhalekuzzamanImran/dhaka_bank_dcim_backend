from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("traps", "0003_snmptrapoidmapping_resolves_event_code"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="snmptrapevent",
            index=models.Index(fields=["device", "received_at"], name="snmp_trap_event_device_time"),
        ),
    ]
