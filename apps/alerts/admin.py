from django.contrib import admin

from .models import (
    AlertComment,
    AlertConditionState,
    AlertEscalationPolicy,
    AlertEvent,
    AlertEventLog,
    AlertRule,
    AlertSuppressionWindow,
)


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "data_center", "device_type", "device", "metric", "severity", "duration_seconds", "is_active")
    list_filter = ("severity", "is_active", "organization", "data_center", "device_type", "device", "metric")
    search_fields = ("name", "metric__code", "device__name")


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "severity",
        "status",
        "organization",
        "data_center",
        "device",
        "metric",
        "triggered_at",
        "acknowledged_at",
        "resolved_at",
        "occurrence_count",
    )
    list_filter = ("severity", "status", "organization", "data_center", "metric", "triggered_at")
    search_fields = ("message", "device__name", "metric__code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AlertConditionState)
class AlertConditionStateAdmin(admin.ModelAdmin):
    list_display = ("device", "metric", "alert_rule", "condition_is_active", "first_seen_at", "last_seen_at")
    list_filter = ("condition_is_active", "alert_rule", "metric")
    search_fields = ("device__name", "metric__code", "alert_rule__name")


@admin.register(AlertEventLog)
class AlertEventLogAdmin(admin.ModelAdmin):
    list_display = ("alert_event", "action", "old_status", "new_status", "actor", "created_at")
    list_filter = ("action", "old_status", "new_status", "created_at")
    search_fields = ("alert_event__message", "message")


@admin.register(AlertComment)
class AlertCommentAdmin(admin.ModelAdmin):
    list_display = ("alert_event", "user", "created_at")
    search_fields = ("comment", "alert_event__message", "user__username")


@admin.register(AlertEscalationPolicy)
class AlertEscalationPolicyAdmin(admin.ModelAdmin):
    list_display = ("severity", "organization", "data_center", "if_not_acknowledged_minutes", "if_not_resolved_minutes", "target_role", "channel", "is_active")
    list_filter = ("severity", "channel", "is_active", "organization", "data_center", "target_role")
    search_fields = ("severity", "target_role__name")


@admin.register(AlertSuppressionWindow)
class AlertSuppressionWindowAdmin(admin.ModelAdmin):
    list_display = ("organization", "data_center", "device", "metric", "starts_at", "ends_at", "is_active")
    list_filter = ("is_active", "organization", "data_center", "device", "metric")
    search_fields = ("reason", "device__name", "metric__code")
