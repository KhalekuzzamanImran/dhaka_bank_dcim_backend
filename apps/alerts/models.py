from django.db import models
from apps.common.models import TimeStampedModel


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
