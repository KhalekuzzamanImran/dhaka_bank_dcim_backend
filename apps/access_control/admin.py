from django.contrib import admin
from .models import Role, Permission, RolePermission, UserDataCenterRole

admin.site.register(Role)
admin.site.register(Permission)
admin.site.register(RolePermission)
admin.site.register(UserDataCenterRole)
