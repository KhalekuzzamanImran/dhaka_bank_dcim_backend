from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "status", "recipient", "subject", "sent_at", "created_at")
    list_filter = ("channel", "status", "created_at", "sent_at")
    search_fields = (
        "recipient__username",
        "recipient__email",
        "subject",
        "message",
    )
    readonly_fields = ("created_at", "updated_at", "sent_at", "error_message")
