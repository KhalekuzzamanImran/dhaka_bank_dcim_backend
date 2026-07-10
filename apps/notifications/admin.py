from django.contrib import admin

from .models import Notification


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


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "status", "recipient", "subject", "read_at", "sent_at", "created_at")
    list_filter = ("channel", "status", "read_at", "created_at", "sent_at", ReadStateFilter)
    search_fields = (
        "recipient__username",
        "recipient__email",
        "subject",
        "message",
    )
    readonly_fields = ("created_at", "updated_at", "sent_at", "read_at", "error_message")
