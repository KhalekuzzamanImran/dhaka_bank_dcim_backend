from django.conf import settings
from django.db import models
from apps.common.models import TimeStampedModel, StatusChoices

class RoleScope(models.TextChoices):
    GLOBAL = "GLOBAL", "Global"
    ORGANIZATION = "ORGANIZATION", "Organization"
    DATA_CENTER = "DATA_CENTER", "Data Center"
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

class UserDataCenterRole(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="data_center_roles")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="user_roles")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="user_roles", blank=True, null=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="assigned_roles", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "user_data_center_roles"
        indexes = [models.Index(fields=["user", "organization"]), models.Index(fields=["user", "data_center"]), models.Index(fields=["role"]), models.Index(fields=["is_active"])]
        constraints = [models.UniqueConstraint(fields=["user", "organization", "data_center", "role"], name="uq_user_org_dc_role")]
    def __str__(self):
        return f"{self.user} - {self.role}"
