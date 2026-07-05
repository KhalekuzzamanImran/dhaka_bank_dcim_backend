from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import Role, Permission, RolePermission, UserResourceAccess

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

class UserResourceAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserResourceAccess
        fields = "__all__"

    def validate(self, attrs):
        instance = self.instance or UserResourceAccess()
        for key, value in attrs.items():
            setattr(instance, key, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs


# Backward-compatible aliases for existing imports.
UserDataCenterRoleSerializer = UserResourceAccessSerializer
