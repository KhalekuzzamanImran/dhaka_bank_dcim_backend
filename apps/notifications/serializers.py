from rest_framework import serializers

from .models import Notification, NotificationDelivery


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)
    is_unread = serializers.BooleanField(read_only=True)
    delivery_summary = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = (
            "id",
            "organization",
            "recipient",
            "subject",
            "message",
            "read_at",
            "metadata",
            "is_read",
            "is_unread",
            "delivery_summary",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_delivery_summary(self, obj):
        summary = {}
        deliveries = getattr(obj, "deliveries", None)
        if deliveries is None:
            return summary
        for delivery in deliveries.all():
            summary[delivery.channel] = delivery.status
        return summary


class NotificationDeliverySerializer(serializers.ModelSerializer):
    notification_subject = serializers.CharField(source="notification.subject", read_only=True)
    notification_message = serializers.CharField(source="notification.message", read_only=True)
    recipient_id = serializers.SerializerMethodField()

    class Meta:
        model = NotificationDelivery
        fields = (
            "id",
            "notification",
            "notification_subject",
            "notification_message",
            "recipient_id",
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
            "provider_response",
            "error_message",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_recipient_id(self, obj):
        return getattr(obj.notification, "recipient_id", None)
