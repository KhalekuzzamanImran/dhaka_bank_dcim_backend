from django.db import models

from apps.common.models import TimeStampedModel


class NotificationChannel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    SMS = "SMS", "SMS"
    WEB = "WEB", "Web"
    WEBHOOK = "WEBHOOK", "Webhook"

class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    DELIVERING = "DELIVERING", "Delivering"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"


class Notification(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="notifications")
    recipient = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="notifications", blank=True, null=True)
    dedupe_key = models.CharField(max_length=255, blank=True, null=True, unique=True)
    subject = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()
    read_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Deprecated compatibility fields. These are no longer the source of truth for delivery state.
    channel = models.CharField(max_length=30, choices=NotificationChannel.choices, blank=True, null=True)
    status = models.CharField(max_length=30, choices=NotificationStatus.choices, blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def is_unread(self):
        return self.read_at is None

    @property
    def delivery_summary(self):
        summary = {}
        deliveries = getattr(self, "deliveries", None)
        if deliveries is None:
            return summary
        for delivery in deliveries.all():
            summary[delivery.channel] = delivery.status
        return summary

    class Meta:
        db_table = "notifications"
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["recipient"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["status"]),
            models.Index(fields=["dedupe_key"]),
            models.Index(fields=["read_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.subject or self.message or str(self.pk)


class NotificationDelivery(TimeStampedModel):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries")
    report_delivery = models.ForeignKey(
        "reports.ReportDelivery",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="notification_deliveries",
    )
    channel = models.CharField(max_length=30, choices=NotificationChannel.choices)
    status = models.CharField(max_length=30, choices=NotificationStatus.choices, default=NotificationStatus.PENDING, db_index=True)
    recipient_address = models.CharField(max_length=255, blank=True, null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    queued_at = models.DateTimeField(blank=True, null=True)
    delivering_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    next_retry_at = models.DateTimeField(blank=True, null=True)
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    provider_response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "notification_deliveries"
        constraints = [
            models.UniqueConstraint(fields=["notification", "channel"], name="unique_notification_delivery_channel"),
        ]
        indexes = [
            models.Index(fields=["notification"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["status"]),
            models.Index(fields=["next_retry_at"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["sent_at"]),
        ]

    def __str__(self):
        return f"{self.notification_id}:{self.channel}:{self.status}"
