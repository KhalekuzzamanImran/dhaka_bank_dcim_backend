from django.db import models
from apps.common.models import TimeStampedModel

class MetricDataType(models.TextChoices):
    FLOAT = "FLOAT", "Float"
    INTEGER = "INTEGER", "Integer"
    BOOLEAN = "BOOLEAN", "Boolean"
    TEXT = "TEXT", "Text"

class MetricCategory(models.TextChoices):
    POWER = "POWER", "Power"
    ENERGY = "ENERGY", "Energy"
    COOLING = "COOLING", "Cooling"
    ENVIRONMENT = "ENVIRONMENT", "Environment"
    STATUS = "STATUS", "Status"
    ALARM = "ALARM", "Alarm"
    NETWORK = "NETWORK", "Network"
    OTHER = "OTHER", "Other"

class MetricDefinition(TimeStampedModel):
    code = models.CharField(max_length=150, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=50, choices=MetricCategory.choices)
    data_type = models.CharField(max_length=50, choices=MetricDataType.choices, default=MetricDataType.FLOAT)
    unit = models.CharField(max_length=50, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "metric_definitions"
        indexes = [models.Index(fields=["code"]), models.Index(fields=["category"]), models.Index(fields=["is_active"])]
    def __str__(self): return self.code

class TelemetryQuality(models.TextChoices):
    GOOD = "GOOD", "Good"
    BAD = "BAD", "Bad"
    STALE = "STALE", "Stale"
    UNKNOWN = "UNKNOWN", "Unknown"

class TelemetryPoint(models.Model):
    time = models.DateTimeField(db_index=True)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="telemetry_points")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="telemetry_points")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="telemetry_points")
    metric = models.ForeignKey(MetricDefinition, on_delete=models.PROTECT, related_name="telemetry_points")
    value_float = models.FloatField(blank=True, null=True)
    value_integer = models.BigIntegerField(blank=True, null=True)
    value_boolean = models.BooleanField(blank=True, null=True)
    value_text = models.TextField(blank=True, null=True)
    raw_value_text = models.TextField(blank=True, null=True)
    quality = models.CharField(max_length=20, choices=TelemetryQuality.choices, default=TelemetryQuality.GOOD, db_index=True)
    source = models.CharField(max_length=50, blank=True, null=True)
    ingest_id = models.UUIDField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "telemetry_points"
        indexes = [models.Index(fields=["device", "metric", "time"]), models.Index(fields=["data_center", "time"]), models.Index(fields=["organization", "time"]), models.Index(fields=["metric", "time"]), models.Index(fields=["quality"]), models.Index(fields=["ingest_id"])]
    def __str__(self): return f"{self.device_id} - {self.metric_id} - {self.time}"

class LatestTelemetry(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="latest_telemetry")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="latest_telemetry")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="latest_telemetry")
    metric = models.ForeignKey(MetricDefinition, on_delete=models.PROTECT, related_name="latest_telemetry")
    value_float = models.FloatField(blank=True, null=True)
    value_integer = models.BigIntegerField(blank=True, null=True)
    value_boolean = models.BooleanField(blank=True, null=True)
    value_text = models.TextField(blank=True, null=True)
    raw_value_text = models.TextField(blank=True, null=True)
    quality = models.CharField(max_length=20, choices=TelemetryQuality.choices, default=TelemetryQuality.GOOD)
    last_seen_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=50, blank=True, null=True)
    class Meta:
        db_table = "latest_telemetry"
        constraints = [models.UniqueConstraint(fields=["device", "metric"], name="uq_latest_device_metric")]
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["device", "metric"]), models.Index(fields=["last_seen_at"]), models.Index(fields=["quality"])]
    def __str__(self): return f"{self.device.name} - {self.metric.code}"

class TelemetryIngestLog(TimeStampedModel):
    ingest_id = models.UUIDField(db_index=True)
    device = models.ForeignKey("devices.Device", on_delete=models.SET_NULL, related_name="ingest_logs", blank=True, null=True)
    protocol = models.CharField(max_length=50)
    status = models.CharField(max_length=50)
    raw_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(blank=True, null=True)
    duration_ms = models.PositiveIntegerField(blank=True, null=True)
    class Meta:
        db_table = "telemetry_ingest_logs"
        indexes = [models.Index(fields=["ingest_id"]), models.Index(fields=["device"]), models.Index(fields=["protocol"]), models.Index(fields=["status"]), models.Index(fields=["started_at"])]

class DeviceEventSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"

class DeviceEvent(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="device_events")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="device_events")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="events")
    event_code = models.CharField(max_length=150)
    event_name = models.CharField(max_length=255)
    severity = models.CharField(max_length=20, choices=DeviceEventSeverity.choices, default=DeviceEventSeverity.INFO)
    message = models.TextField()
    occurred_at = models.DateTimeField(db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = "device_events"
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["device"]), models.Index(fields=["severity"]), models.Index(fields=["occurred_at"])]
