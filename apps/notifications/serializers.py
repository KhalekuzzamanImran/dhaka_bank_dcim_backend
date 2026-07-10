from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)
    is_unread = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "organization",
            "recipient",
            "channel",
            "subject",
            "message",
            "status",
            "sent_at",
            "read_at",
            "error_message",
            "metadata",
            "is_read",
            "is_unread",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "status", "sent_at", "read_at", "error_message", "is_read", "is_unread", "created_at", "updated_at")
