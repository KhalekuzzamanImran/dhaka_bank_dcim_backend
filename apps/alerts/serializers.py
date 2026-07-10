from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from .models import (
    AlertComment,
    AlertConditionState,
    AlertEscalationPolicy,
    AlertEvent,
    AlertEventLog,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    AlertSuppressionWindow,
)


def _display_name(user: User | None) -> str | None:
    if not user:
        return None
    full_name = getattr(user, "full_name", None)
    if full_name:
        return full_name
    if getattr(user, "get_full_name", None):
        name = user.get_full_name()
        if name:
            return name
    return user.username


class AlertRuleSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    data_center_name = serializers.CharField(source="data_center.name", read_only=True)
    device_type_name = serializers.CharField(source="device_type.name", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)
    metric_code = serializers.CharField(source="metric.code", read_only=True)
    metric_name = serializers.CharField(source="metric.name", read_only=True)

    class Meta:
        model = AlertRule
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "device_type",
            "device_type_name",
            "device",
            "device_name",
            "metric",
            "metric_code",
            "metric_name",
            "name",
            "operator",
            "threshold_float",
            "threshold_integer",
            "threshold_boolean",
            "threshold_text",
            "severity",
            "duration_seconds",
            "is_active",
            "message_template",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "organization_name", "data_center_name", "device_type_name", "device_name", "metric_code", "metric_name", "created_at", "updated_at")


class AlertEventBaseSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    data_center_name = serializers.CharField(source="data_center.name", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)
    device_code = serializers.CharField(source="device.code", read_only=True)
    metric_code = serializers.CharField(source="metric.code", read_only=True)
    metric_name = serializers.CharField(source="metric.name", read_only=True)
    alert_rule_name = serializers.CharField(source="alert_rule.name", read_only=True)
    acknowledged_by_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()
    room_name = serializers.SerializerMethodField()
    rack_name = serializers.SerializerMethodField()
    age_seconds = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = AlertEvent
        fields = (
            "id",
            "severity",
            "status",
            "message",
            "triggered_at",
            "last_seen_at",
            "acknowledged_at",
            "resolved_at",
            "occurrence_count",
            "resolution_type",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "device",
            "device_name",
            "device_code",
            "room_name",
            "rack_name",
            "metric",
            "metric_code",
            "metric_name",
            "alert_rule",
            "alert_rule_name",
            "acknowledged_by",
            "acknowledged_by_name",
            "resolved_by",
            "resolved_by_name",
            "age_seconds",
            "is_active",
        )
        read_only_fields = fields

    def get_acknowledged_by_name(self, obj):
        return _display_name(obj.acknowledged_by)

    def get_resolved_by_name(self, obj):
        return _display_name(obj.resolved_by)

    def get_room_name(self, obj):
        if obj.device and obj.device.room:
            return obj.device.room.name
        return None

    def get_rack_name(self, obj):
        if obj.device and obj.device.rack:
            return obj.device.rack.name
        return None

    def get_age_seconds(self, obj):
        if not obj.triggered_at:
            return None
        delta = timezone.now() - obj.triggered_at
        return max(0, int(delta.total_seconds()))

    def get_is_active(self, obj):
        return obj.status in {AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED}


class AlertEventListSerializer(AlertEventBaseSerializer):
    class Meta(AlertEventBaseSerializer.Meta):
        fields = AlertEventBaseSerializer.Meta.fields
        read_only_fields = AlertEventBaseSerializer.Meta.read_only_fields


class AlertEventLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AlertEventLog
        fields = (
            "id",
            "alert_event",
            "action",
            "old_status",
            "new_status",
            "actor",
            "actor_name",
            "message",
            "value_snapshot",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_actor_name(self, obj):
        return _display_name(obj.actor)


class AlertCommentSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AlertComment
        fields = ("id", "alert_event", "user", "user_name", "comment", "created_at", "updated_at")
        read_only_fields = ("id", "user", "user_name", "created_at", "updated_at")

    def get_user_name(self, obj):
        return _display_name(obj.user)


class AlertEventDetailSerializer(AlertEventListSerializer):
    comments = AlertCommentSerializer(many=True, read_only=True)
    logs = AlertEventLogSerializer(many=True, read_only=True)

    class Meta(AlertEventListSerializer.Meta):
        fields = AlertEventListSerializer.Meta.fields + (
            "acknowledge_comment",
            "resolve_comment",
            "metadata",
            "comments",
            "logs",
        )
        read_only_fields = AlertEventListSerializer.Meta.read_only_fields + ("acknowledge_comment", "resolve_comment", "metadata")


class AlertAcknowledgeSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000, trim_whitespace=True)


class AlertResolveSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000, trim_whitespace=True)


class AlertConditionStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertConditionState
        fields = "__all__"


class AlertEscalationPolicySerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    data_center_name = serializers.CharField(source="data_center.name", read_only=True)
    target_role_name = serializers.CharField(source="target_role.name", read_only=True)

    class Meta:
        model = AlertEscalationPolicy
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "severity",
            "if_not_acknowledged_minutes",
            "if_not_resolved_minutes",
            "target_role",
            "target_role_name",
            "target_users",
            "channel",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "organization_name", "data_center_name", "target_role_name", "created_at", "updated_at")

    def validate(self, attrs):
        target_role = attrs.get("target_role", getattr(self.instance, "target_role", None))
        if "target_users" in attrs:
            target_users = attrs["target_users"]
        elif self.instance is not None:
            target_users = list(self.instance.target_users.all())
        else:
            target_users = []
        if target_role is None and not target_users:
            raise serializers.ValidationError({"target_role": "At least one target must be configured."})
        return attrs


class AlertSuppressionWindowSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    data_center_name = serializers.CharField(source="data_center.name", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)
    metric_code = serializers.CharField(source="metric.code", read_only=True)

    class Meta:
        model = AlertSuppressionWindow
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "device",
            "device_name",
            "metric",
            "metric_code",
            "starts_at",
            "ends_at",
            "reason",
            "created_by",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "organization_name", "data_center_name", "device_name", "metric_code", "created_at", "updated_at")


# Backward-compatible alias used by older imports and generic views.
AlertEventSerializer = AlertEventDetailSerializer
