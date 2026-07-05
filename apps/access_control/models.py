from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from apps.common.models import TimeStampedModel, StatusChoices

class RoleScope(models.TextChoices):
    GLOBAL = "GLOBAL", "Global"
    ORGANIZATION = "ORGANIZATION", "Organization"
    DATA_CENTER = "DATA_CENTER", "Data Center"
    ROOM = "ROOM", "Room"
    RACK = "RACK", "Rack"
    DEVICE = "DEVICE", "Device"

class Role(TimeStampedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=100, unique=True)
    scope = models.CharField(max_length=50, choices=RoleScope.choices, default=RoleScope.DATA_CENTER)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.ACTIVE)
    class Meta:
        db_table = "roles"
    def __str__(self):
        return self.name

class Permission(TimeStampedModel):
    module = models.CharField(max_length=100)
    code = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "permissions"
        indexes = [models.Index(fields=["module"]), models.Index(fields=["code"])]
    def __str__(self):
        return self.code

class RolePermission(TimeStampedModel):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="permission_roles")
    class Meta:
        db_table = "role_permissions"
        constraints = [models.UniqueConstraint(fields=["role", "permission"], name="uq_role_permission")]

class UserResourceAccess(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="data_center_roles")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="user_roles", blank=True, null=True)
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="user_roles", blank=True, null=True)
    room = models.ForeignKey("datacenters.Room", on_delete=models.CASCADE, related_name="user_roles", blank=True, null=True)
    rack = models.ForeignKey("datacenters.Rack", on_delete=models.CASCADE, related_name="user_roles", blank=True, null=True)
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="user_roles", blank=True, null=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="assigned_roles", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "user_resource_accesses"
        indexes = [
            models.Index(fields=["user", "organization"], name="user_data_c_user_id_4e915c_idx"),
            models.Index(fields=["user", "data_center"], name="user_data_c_user_id_c766e8_idx"),
            models.Index(fields=["role"], name="user_data_c_role_id_5bfca8_idx"),
            models.Index(fields=["is_active"], name="user_data_c_is_acti_316762_idx"),
            models.Index(fields=["user", "room"], name="user_reso_user_room_idx"),
            models.Index(fields=["user", "rack"], name="user_reso_user_rack_idx"),
            models.Index(fields=["user", "device"], name="user_reso_user_device_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user", "organization", "data_center", "room", "rack", "device", "role"], name="uq_user_resource_access"),
        ]
    def __str__(self):
        return f"{self.user} - {self.role}"

    def clean(self):
        super().clean()

        errors = {}
        scope = getattr(self.role, "scope", None)

        def _ensure_blank(field_name):
            if getattr(self, field_name) is not None:
                errors[field_name] = "Must be blank for this role scope."

        def _ensure_required(field_name):
            if getattr(self, field_name) is None:
                errors[field_name] = "This field is required for this role scope."

        if scope == RoleScope.GLOBAL:
            if any(getattr(self, field) is not None for field in ("organization", "data_center", "room", "rack", "device")):
                errors["non_field_errors"] = "Global access must not include organization, data center, room, rack, or device."
        elif scope == RoleScope.ORGANIZATION:
            _ensure_required("organization")
            for field in ("data_center", "room", "rack", "device"):
                _ensure_blank(field)
        elif scope == RoleScope.DATA_CENTER:
            _ensure_required("organization")
            _ensure_required("data_center")
            for field in ("room", "rack", "device"):
                _ensure_blank(field)
        elif scope == RoleScope.ROOM:
            _ensure_required("organization")
            _ensure_required("data_center")
            _ensure_required("room")
            for field in ("rack", "device"):
                _ensure_blank(field)
        elif scope == RoleScope.RACK:
            _ensure_required("organization")
            _ensure_required("data_center")
            _ensure_required("room")
            _ensure_required("rack")
            _ensure_blank("device")
        elif scope == RoleScope.DEVICE:
            _ensure_required("device")
        else:
            errors["role"] = "Role scope is not supported."

        device = self.device
        if device is not None:
            # Device access can be assigned with only the device selected; the
            # parent hierarchy is back-filled so the access row stays consistent.
            if self.organization is None:
                self.organization = device.organization
            elif device.organization_id != self.organization_id:
                errors["organization"] = "Must match the selected device."

            if self.data_center is None and device.data_center_id is not None:
                self.data_center = device.data_center
            elif device.data_center_id is not None and self.data_center_id != device.data_center_id:
                errors["data_center"] = "Must match the selected device."

            if self.room is None and device.room_id is not None:
                self.room = device.room
            elif device.room_id is not None and self.room_id != device.room_id:
                errors["room"] = "Must match the selected device."

            if self.rack is None and device.rack_id is not None:
                self.rack = device.rack
            elif device.rack_id is not None and self.rack_id != device.rack_id:
                errors["rack"] = "Must match the selected device."

        if self.room is not None and self.data_center is not None and self.room.data_center_id != self.data_center_id:
            errors["room"] = "Room must belong to the selected data center."
        if self.rack is not None:
            if self.room is not None and self.rack.room_id != self.room_id:
                errors["rack"] = "Rack must belong to the selected room."
            if self.data_center is not None and self.rack.data_center_id != self.data_center_id:
                errors["rack"] = "Rack must belong to the selected data center."
        if self.data_center is not None and self.organization is not None and self.data_center.organization_id != self.organization_id:
            errors["data_center"] = "Data center must belong to the selected organization."

        if scope == RoleScope.DEVICE and self.device is None:
            errors["device"] = "This field is required for device scope."

        if scope in {RoleScope.ORGANIZATION, RoleScope.DATA_CENTER, RoleScope.ROOM, RoleScope.RACK, RoleScope.DEVICE} and self.organization is None and scope != RoleScope.DEVICE:
            errors["organization"] = "This field is required for the selected role scope."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


# Backward-compatible alias for existing imports and legacy code paths.
UserDataCenterRole = UserResourceAccess
