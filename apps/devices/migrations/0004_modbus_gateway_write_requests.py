import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("devices", "0003_modbus_reverse_gateways")]

    operations = [
        migrations.CreateModel(
            name="ModbusGatewayWriteRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("register_address", models.PositiveIntegerField()),
                ("register_value", models.PositiveIntegerField()),
                ("slave_id", models.PositiveSmallIntegerField(default=1)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("SUCCEEDED", "Succeeded"), ("FAILED", "Failed")], default="PENDING", max_length=20)),
                ("response_value", models.PositiveIntegerField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="modbus_gateway_write_requests", to="devices.device")),
                ("gateway", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="write_requests", to="devices.modbusgateway")),
            ],
            options={
                "db_table": "modbus_gateway_write_requests",
                "indexes": [models.Index(fields=["gateway", "status", "created_at"], name="modbus_gate_gateway_5c267a_idx")],
            },
        ),
    ]
