from django.db import models
from apps.common.models import TimeStampedModel

class NotificationChannel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    SMS = "SMS", "SMS"
    WEB = "WEB", "Web"
    WEBHOOK = "WEBHOOK", "Webhook"

class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"

class Notification(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="notifications")
    recipient = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="notifications", blank=True, null=True)
    channel = models.CharField(max_length=30, choices=NotificationChannel.choices)
    subject = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()
    status = models.CharField(max_length=30, choices=NotificationStatus.choices, default=NotificationStatus.PENDING)
    sent_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = "notifications"
        indexes = [models.Index(fields=["organization"]), models.Index(fields=["recipient"]), models.Index(fields=["channel"]), models.Index(fields=["status"]), models.Index(fields=["created_at"])]
