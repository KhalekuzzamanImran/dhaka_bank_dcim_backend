from __future__ import annotations

from rest_framework import serializers

from apps.common.audit import redact

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    organization_name = serializers.SerializerMethodField()
    actor_name = serializers.SerializerMethodField()
    actor_email = serializers.SerializerMethodField()
    old_value = serializers.SerializerMethodField()
    new_value = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "organization",
            "organization_name",
            "actor",
            "actor_name",
            "actor_email",
            "action",
            "resource_type",
            "resource_id",
            "old_value",
            "new_value",
            "ip_address",
            "user_agent",
            "message",
            "created_at",
        )
        read_only_fields = fields

    def get_actor_name(self, obj):
        if not obj.actor_id:
            return None
        return getattr(obj.actor, "full_name", None) or getattr(obj.actor, "username", None) or getattr(obj.actor, "email", None)

    def get_organization_name(self, obj):
        if not obj.organization_id:
            return None
        return getattr(obj.organization, "name", None)

    def get_actor_email(self, obj):
        if not obj.actor_id:
            return None
        return getattr(obj.actor, "email", None)

    def get_old_value(self, obj):
        return redact(obj.old_value or {})

    def get_new_value(self, obj):
        return redact(obj.new_value or {})
