from django.contrib import admin

from .models import Notification, NotificationDelivery


class ReadStateFilter(admin.SimpleListFilter):
    title = "read state"
    parameter_name = "read_state"

    def lookups(self, request, model_admin):
        return (
            ("read", "Read"),
            ("unread", "Unread"),
        )

    def queryset(self, request, queryset):
        if self.value() == "read":
            return queryset.filter(read_at__isnull=False)
        if self.value() == "unread":
            return queryset.filter(read_at__isnull=True)
        return queryset


class NotificationDeliveryInline(admin.TabularInline):
    model = NotificationDelivery
    extra = 0
    fields = (
        "channel",
        "status",
        "recipient_address",
        "attempt_count",
        "max_attempts",
        "queued_at",
        "delivering_at",
        "sent_at",
        "failed_at",
        "next_retry_at",
        "provider_message_id",
        "error_message",
    )
    readonly_fields = fields
    can_delete = False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("subject", "recipient", "channel_summary", "status_summary", "read_state", "created_at", "updated_at")
    list_filter = ("read_at", "created_at", ReadStateFilter)
    search_fields = (
        "recipient__username",
        "recipient__email",
        "subject",
        "message",
        "dedupe_key",
    )
    readonly_fields = ("created_at", "updated_at", "read_at", "metadata", "channel", "status", "sent_at", "error_message")
    ordering = ("-created_at", "-updated_at", "-id")
    inlines = [NotificationDeliveryInline]

    @admin.display(description="Channels")
    def channel_summary(self, obj):
        channels = obj.deliveries.values_list("channel", flat=True)
        return ", ".join(channels) or obj.channel or "Web"

    @admin.display(description="Delivery status")
    def status_summary(self, obj):
        statuses = obj.deliveries.values_list("status", flat=True)
        return ", ".join(statuses) or obj.status or "Pending"

    @admin.display(description="Read")
    def read_state(self, obj):
        return "Read" if obj.read_at else "Unread"


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ("notification_subject", "channel", "status", "recipient_address", "sent_at", "failed_at", "created_at")
    list_filter = ("channel", "status", "created_at", "sent_at", "failed_at")
    search_fields = (
        "notification__subject",
        "notification__message",
        "recipient_address",
        "error_message",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "queued_at",
        "delivering_at",
        "sent_at",
        "failed_at",
        "next_retry_at",
        "provider_message_id",
        "provider_response",
        "error_message",
        "metadata",
    )
    list_select_related = ("notification",)
    ordering = ("-created_at", "-updated_at", "-id")

    @admin.display(description="Notification")
    def notification_subject(self, obj):
        return obj.notification.subject or obj.notification.message[:80]
