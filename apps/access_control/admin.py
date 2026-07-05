from django.contrib import admin

from .models import Permission, Role, RolePermission, UserResourceAccess


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "scope", "status")
    list_filter = ("scope", "status")
    search_fields = ("name", "code")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("module", "code", "description")
    list_filter = ("module",)
    search_fields = ("module", "code", "description")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "created_at")
    list_filter = ("role", "permission")
    search_fields = ("role__name", "role__code", "permission__code")


@admin.register(UserResourceAccess)
class UserResourceAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "organization", "data_center", "room", "rack", "device", "is_active", "assigned_by", "created_at")
    list_filter = ("role", "organization", "data_center", "room", "rack", "device", "is_active")
    search_fields = ("user__username", "user__email", "role__name", "role__code")
