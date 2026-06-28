from rest_framework import serializers
from .models import Role, Permission, RolePermission, UserDataCenterRole

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = "__all__"

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = "__all__"

class RolePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RolePermission
        fields = "__all__"

class UserDataCenterRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDataCenterRole
        fields = "__all__"
