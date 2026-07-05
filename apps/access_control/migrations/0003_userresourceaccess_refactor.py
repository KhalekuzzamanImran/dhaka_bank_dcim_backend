# Generated manually to preserve access-row data while renaming the model and extending the scope hierarchy.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access_control", "0002_initial"),
        ("datacenters", "0001_initial"),
        ("devices", "0001_initial"),
        ("organizations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name="UserDataCenterRole",
            new_name="UserResourceAccess",
        ),
        migrations.AlterModelTable(
            name="userresourceaccess",
            table="user_resource_accesses",
        ),
        migrations.AlterField(
            model_name="userresourceaccess",
            name="organization",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="user_roles", to="organizations.organization"),
        ),
        migrations.AddField(
            model_name="userresourceaccess",
            name="room",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="user_roles", to="datacenters.room"),
        ),
        migrations.AddField(
            model_name="userresourceaccess",
            name="rack",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="user_roles", to="datacenters.rack"),
        ),
        migrations.AddField(
            model_name="userresourceaccess",
            name="device",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="user_roles", to="devices.device"),
        ),
        migrations.AddIndex(
            model_name="userresourceaccess",
            index=models.Index(fields=["user", "room"], name="user_reso_user_room_idx"),
        ),
        migrations.AddIndex(
            model_name="userresourceaccess",
            index=models.Index(fields=["user", "rack"], name="user_reso_user_rack_idx"),
        ),
        migrations.AddIndex(
            model_name="userresourceaccess",
            index=models.Index(fields=["user", "device"], name="user_reso_user_device_idx"),
        ),
        migrations.RemoveConstraint(
            model_name="userresourceaccess",
            name="uq_user_org_dc_role",
        ),
        migrations.AddConstraint(
            model_name="userresourceaccess",
            constraint=models.UniqueConstraint(fields=("user", "organization", "data_center", "room", "rack", "device", "role"), name="uq_user_resource_access"),
        ),
    ]
