from django.db import models
from apps.common.models import TimeStampedModel

class AlertSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"

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
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["device_type"]), models.Index(fields=["device"]), models.Index(fields=["metric"]), models.Index(fields=["severity"]), models.Index(fields=["is_active"])]
    def __str__(self): return self.name

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
    acknowledged_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="acknowledged_alerts", blank=True, null=True)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    class Meta:
        db_table = "alert_events"
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["device", "status"]), models.Index(fields=["severity", "status"]), models.Index(fields=["triggered_at"]), models.Index(fields=["status"])]
    def __str__(self): return self.message
