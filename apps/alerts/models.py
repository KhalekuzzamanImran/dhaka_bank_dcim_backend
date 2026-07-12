import logging

from django.core.exceptions import ValidationError
from django.db import models, transaction
from apps.common.models import TimeStampedModel

logger = logging.getLogger(__name__)


def invalidate_alert_rule_match_cache():
    from .services.cache import invalidate_alert_rule_match_cache as _invalidate

    return _invalidate()


class AlertSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"
    EMERGENCY = "EMERGENCY", "Emergency"

class AlertOperator(models.TextChoices):
    GT = "GT", "Greater Than"
    GTE = "GTE", "Greater Than or Equal"
    LT = "LT", "Less Than"
    LTE = "LTE", "Less Than or Equal"
    EQ = "EQ", "Equal"
    NEQ = "NEQ", "Not Equal"

class AlertRule(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="alert_rules")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="alert_rules", blank=True, null=True)
    device_type = models.ForeignKey("devices.DeviceType", on_delete=models.CASCADE, related_name="alert_rules", blank=True, null=True)
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="alert_rules", blank=True, null=True)
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.CASCADE, related_name="alert_rules")
    name = models.CharField(max_length=255)
    operator = models.CharField(max_length=20, choices=AlertOperator.choices)
    threshold_float = models.FloatField(blank=True, null=True)
    threshold_integer = models.BigIntegerField(blank=True, null=True)
    threshold_boolean = models.BooleanField(blank=True, null=True)
    threshold_text = models.CharField(max_length=255, blank=True, null=True)
    severity = models.CharField(max_length=20, choices=AlertSeverity.choices, default=AlertSeverity.WARNING)
    duration_seconds = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    message_template = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "alert_rules"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["device_type"]),
            models.Index(fields=["device"]),
            models.Index(fields=["metric"]),
            models.Index(fields=["severity"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()

        errors = {}

        threshold_fields = (
            "threshold_float",
            "threshold_integer",
            "threshold_boolean",
            "threshold_text",
        )
        set_threshold_fields = [field for field in threshold_fields if getattr(self, field) is not None]
        if len(set_threshold_fields) != 1:
            message = "Exactly one threshold field must be set."
            for field in threshold_fields:
                errors.setdefault(field, []).append(message)

        if self.device_id:
            device = self.device
            if self.organization_id and device.organization_id != self.organization_id:
                errors.setdefault("organization", []).append("Must match the selected device organization.")
            if self.data_center_id and device.data_center_id != self.data_center_id:
                errors.setdefault("data_center", []).append("Must match the selected device data center.")
            if self.device_type_id and device.device_type_id != self.device_type_id:
                errors.setdefault("device_type", []).append("Must match the selected device type.")

        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if not errors and self.is_active:
            threshold_field = set_threshold_fields[0]
            duplicate_qs = AlertRule.objects.filter(
                is_active=True,
                organization_id=self.organization_id,
                data_center_id=self.data_center_id,
                device_type_id=self.device_type_id,
                device_id=self.device_id,
                metric_id=self.metric_id,
                operator=self.operator,
            )
            for field in threshold_fields:
                value = getattr(self, field)
                if field == threshold_field:
                    duplicate_qs = duplicate_qs.filter(**{field: value})
                else:
                    duplicate_qs = duplicate_qs.filter(**{f"{field}__isnull": True})
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                errors.setdefault("__all__", []).append(
                    "An active alert rule with the same scope, operator, metric, and threshold already exists."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)

        def _invalidate_after_commit():
            try:
                invalidate_alert_rule_match_cache()
            except Exception:
                # Cache invalidation must never block a rule write.
                logger.warning(
                    "Failed to invalidate alert rule matching cache after save.",
                    exc_info=True,
                )

        transaction.on_commit(_invalidate_after_commit)
        return result

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)

        def _invalidate_after_commit():
            try:
                invalidate_alert_rule_match_cache()
            except Exception:
                logger.warning(
                    "Failed to invalidate alert rule matching cache after delete.",
                    exc_info=True,
                )

        transaction.on_commit(_invalidate_after_commit)
        return result

class AlertStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
    RESOLVED = "RESOLVED", "Resolved"
    SUPPRESSED = "SUPPRESSED", "Suppressed"

class AlertEvent(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="alert_events")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="alert_events")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="alert_events")
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.PROTECT, related_name="alert_events", blank=True, null=True)
    alert_rule = models.ForeignKey(AlertRule, on_delete=models.SET_NULL, related_name="alert_events", blank=True, null=True)
    severity = models.CharField(max_length=20, choices=AlertSeverity.choices, db_index=True)
    status = models.CharField(max_length=30, choices=AlertStatus.choices, default=AlertStatus.OPEN, db_index=True)
    value_float = models.FloatField(blank=True, null=True)
    value_integer = models.BigIntegerField(blank=True, null=True)
    value_boolean = models.BooleanField(blank=True, null=True)
    value_text = models.TextField(blank=True, null=True)
    message = models.TextField()
    triggered_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(blank=True, null=True, db_index=True)
    acknowledged_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="acknowledged_alerts", blank=True, null=True)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="resolved_alerts",
        blank=True,
        null=True,
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolution_type = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ("AUTO", "Auto"),
            ("MANUAL", "Manual"),
            ("SUPPRESSED", "Suppressed"),
            ("EXPIRED", "Expired"),
        ],
    )
    acknowledge_comment = models.TextField(blank=True, null=True)
    resolve_comment = models.TextField(blank=True, null=True)
    occurrence_count = models.PositiveIntegerField(default=1)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = "alert_events"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["device", "status"]),
            models.Index(fields=["severity", "status"]),
            models.Index(fields=["triggered_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["last_seen_at"]),
        ]

    def __str__(self):
        return self.message


class AlertConditionState(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="alert_condition_states")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="alert_condition_states")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="alert_condition_states")
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.PROTECT, related_name="alert_condition_states")
    alert_rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="condition_states")
    first_seen_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(db_index=True)
    last_value_float = models.FloatField(blank=True, null=True)
    last_value_integer = models.BigIntegerField(blank=True, null=True)
    last_value_boolean = models.BooleanField(blank=True, null=True)
    last_value_text = models.TextField(blank=True, null=True)
    condition_is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "alert_condition_states"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "data_center", "device", "metric", "alert_rule"],
                name="uq_alert_condition_state",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["device", "metric"]),
            models.Index(fields=["alert_rule"]),
            models.Index(fields=["condition_is_active"]),
        ]

    def __str__(self):
        return f"{self.device_id}:{self.metric_id}:{self.alert_rule_id}"


class AlertEventLogAction(models.TextChoices):
    OPENED = "OPENED", "Opened"
    UPDATED = "UPDATED", "Updated"
    ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
    RESOLVED = "RESOLVED", "Resolved"
    SUPPRESSED = "SUPPRESSED", "Suppressed"
    ESCALATED = "ESCALATED", "Escalated"
    COMMENTED = "COMMENTED", "Commented"
    NOTIFICATION_CREATED = "NOTIFICATION_CREATED", "Notification Created"
    NOTIFICATION_SENT = "NOTIFICATION_SENT", "Notification Sent"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED", "Notification Failed"


class AlertEventLog(TimeStampedModel):
    alert_event = models.ForeignKey("alerts.AlertEvent", on_delete=models.CASCADE, related_name="logs")
    action = models.CharField(max_length=50, choices=AlertEventLogAction.choices, db_index=True)
    old_status = models.CharField(max_length=30, blank=True, null=True)
    new_status = models.CharField(max_length=30, blank=True, null=True)
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="alert_event_logs", blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    value_snapshot = models.JSONField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "alert_event_logs"
        indexes = [
            models.Index(fields=["alert_event", "action"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.action} {self.alert_event_id}"


class AlertComment(TimeStampedModel):
    alert_event = models.ForeignKey("alerts.AlertEvent", on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="alert_comments", blank=True, null=True)
    comment = models.TextField()

    class Meta:
        db_table = "alert_comments"
        indexes = [models.Index(fields=["alert_event"]), models.Index(fields=["user"]), models.Index(fields=["created_at"])]

    def __str__(self):
        return f"{self.alert_event_id}"


class AlertEscalationPolicy(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="alert_escalation_policies", blank=True, null=True)
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="alert_escalation_policies", blank=True, null=True)
    severity = models.CharField(max_length=20, choices=AlertSeverity.choices)
    if_not_acknowledged_minutes = models.PositiveIntegerField(blank=True, null=True)
    if_not_resolved_minutes = models.PositiveIntegerField(blank=True, null=True)
    target_role = models.ForeignKey("access_control.Role", on_delete=models.SET_NULL, related_name="alert_escalation_policies", blank=True, null=True)
    target_users = models.ManyToManyField("accounts.User", blank=True, related_name="alert_escalation_policies")
    channel = models.CharField(
        max_length=20,
        choices=[("WEB", "Web"), ("EMAIL", "Email"), ("SMS", "SMS"), ("WEBHOOK", "Webhook")],
        default="WEB",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "alert_escalation_policies"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["severity"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.severity} escalation policy"

    def clean(self):
        super().clean()

        errors = {}

        if self.if_not_acknowledged_minutes is None and self.if_not_resolved_minutes is None:
            errors.setdefault("if_not_acknowledged_minutes", []).append(
                "At least one escalation trigger must be configured."
            )
            errors.setdefault("if_not_resolved_minutes", []).append(
                "At least one escalation trigger must be configured."
            )

        for field in ("if_not_acknowledged_minutes", "if_not_resolved_minutes"):
            value = getattr(self, field)
            if value is not None and value <= 0:
                errors.setdefault(field, []).append("Must be a positive integer.")

        if self.channel not in {"WEB", "EMAIL", "SMS", "WEBHOOK"}:
            errors.setdefault("channel", []).append("Invalid escalation channel.")

        if self.organization_id and self.data_center_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if self.is_active:
            duplicate_qs = AlertEscalationPolicy.objects.filter(
                is_active=True,
                organization_id=self.organization_id,
                data_center_id=self.data_center_id,
                severity=self.severity,
                if_not_acknowledged_minutes=self.if_not_acknowledged_minutes,
                if_not_resolved_minutes=self.if_not_resolved_minutes,
                target_role_id=self.target_role_id,
                channel=self.channel,
            )
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                errors.setdefault("__all__", []).append(
                    "An active escalation policy with the same scope and trigger already exists."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AlertSuppressionWindow(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="alert_suppression_windows")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="alert_suppression_windows", blank=True, null=True)
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="alert_suppression_windows", blank=True, null=True)
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.CASCADE, related_name="alert_suppression_windows", blank=True, null=True)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    reason = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="created_alert_suppression_windows", blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "alert_suppression_windows"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["device"]),
            models.Index(fields=["metric"]),
            models.Index(fields=["starts_at", "ends_at"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"Suppression {self.organization_id}"

    def clean(self):
        super().clean()

        errors = {}

        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")

        if self.starts_at is None:
            errors.setdefault("starts_at", []).append("Start time is required.")
        if self.ends_at is None:
            errors.setdefault("ends_at", []).append("End time is required.")
        if self.starts_at is not None and self.ends_at is not None and self.starts_at >= self.ends_at:
            errors.setdefault("starts_at", []).append("Start time must be earlier than end time.")
            errors.setdefault("ends_at", []).append("End time must be later than start time.")

        if not any(getattr(self, f"{field}_id") is not None for field in ("organization", "data_center", "device", "metric")):
            errors.setdefault("__all__", []).append(
                "At least one scope field must be selected for a suppression window."
            )

        if self.organization_id and self.data_center_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if self.device_id:
            device = self.device
            if self.organization_id and device.organization_id != self.organization_id:
                errors.setdefault("organization", []).append("Must match the selected device organization.")
            if self.data_center_id and device.data_center_id != self.data_center_id:
                errors.setdefault("data_center", []).append("Must match the selected device data center.")

        if self.is_active and self.starts_at is not None and self.ends_at is not None and self.organization_id is not None:
            overlap_qs = AlertSuppressionWindow.objects.filter(
                is_active=True,
                organization_id=self.organization_id,
                data_center_id=self.data_center_id,
                device_id=self.device_id,
                metric_id=self.metric_id,
            ).filter(starts_at__lt=self.ends_at, ends_at__gt=self.starts_at)
            if self.pk:
                overlap_qs = overlap_qs.exclude(pk=self.pk)
            if overlap_qs.exists():
                errors.setdefault("__all__", []).append(
                    "An active suppression window with the same scope overlaps the selected time range."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
