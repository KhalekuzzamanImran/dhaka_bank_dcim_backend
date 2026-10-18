import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("devices", "0002_initial")]

    operations = [
        migrations.CreateModel(
            name="ModbusGateway",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("code", models.CharField(max_length=100)),
                ("source_ip", models.GenericIPAddressField(help_text="Stable VPN address observed when this gateway connects.", unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("connection_status", models.CharField(choices=[("DISCONNECTED", "Disconnected"), ("CONNECTED", "Connected")], default="DISCONNECTED", max_length=20)),
                ("last_connected_at", models.DateTimeField(blank=True, null=True)),
                ("last_disconnected_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("data_center", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="modbus_gateways", to="datacenters.datacenter")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="modbus_gateways", to="organizations.organization")),
            ],
            options={
                "db_table": "modbus_gateways",
                "indexes": [
                    models.Index(fields=["organization", "data_center"], name="modbus_gate_organiz_03279f_idx"),
                    models.Index(fields=["is_active", "connection_status"], name="modbus_gate_is_acti_2c8a56_idx"),
                ],
                "constraints": [models.UniqueConstraint(fields=("data_center", "code"), name="uq_dc_modbus_gateway_code")],
            },
        ),
        migrations.AddField(
            model_name="deviceprotocolconfig",
            name="modbus_gateway",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="device_configs", to="devices.modbusgateway"),
        ),
        migrations.AlterField(
            model_name="deviceprotocolconfig",
            name="host",
            field=models.CharField(blank=True, help_text="Unused for a reverse-connected RTU gateway.", max_length=255),
        ),
    ]
