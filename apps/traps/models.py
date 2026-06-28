from django.db import models
from apps.common.models import TimeStampedModel


class TrapSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"


class SNMPTrapSource(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="snmp_trap_sources")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="snmp_trap_sources")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="snmp_trap_sources", blank=True, null=True)
    source_ip = models.GenericIPAddressField(db_index=True)
    is_enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "snmp_trap_sources"
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["source_ip"]), models.Index(fields=["is_enabled"])]
        constraints = [models.UniqueConstraint(fields=["source_ip", "data_center"], name="uq_trap_source_ip_dc")]

    def __str__(self):
        return str(self.source_ip)


class SNMPTrapOIDMapping(TimeStampedModel):
    device_type = models.ForeignKey("devices.DeviceType", on_delete=models.CASCADE, related_name="snmp_trap_oid_mappings")
    vendor = models.ForeignKey("devices.Vendor", on_delete=models.CASCADE, related_name="snmp_trap_oid_mappings", blank=True, null=True)
    device_model = models.ForeignKey("devices.DeviceModel", on_delete=models.CASCADE, related_name="snmp_trap_oid_mappings", blank=True, null=True)
    trap_oid = models.CharField(max_length=255, db_index=True)
    event_code = models.CharField(max_length=150)
    event_name = models.CharField(max_length=255)
    severity = models.CharField(max_length=20, choices=TrapSeverity.choices, default=TrapSeverity.INFO)
    message_template = models.TextField(blank=True, null=True)
    create_alert = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "snmp_trap_oid_mappings"
        indexes = [models.Index(fields=["device_type"]), models.Index(fields=["vendor"]), models.Index(fields=["device_model"]), models.Index(fields=["trap_oid"]), models.Index(fields=["is_active"])]

    def __str__(self):
        return f"{self.event_code} - {self.trap_oid}"


class SNMPTrapEvent(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.SET_NULL, related_name="snmp_trap_events", blank=True, null=True)
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.SET_NULL, related_name="snmp_trap_events", blank=True, null=True)
    device = models.ForeignKey("devices.Device", on_delete=models.SET_NULL, related_name="snmp_trap_events", blank=True, null=True)
    source_ip = models.GenericIPAddressField(db_index=True)
    trap_oid = models.CharField(max_length=255, db_index=True)
    event_code = models.CharField(max_length=150, blank=True, null=True)
    event_name = models.CharField(max_length=255, blank=True, null=True)
    severity = models.CharField(max_length=20, default=TrapSeverity.INFO)
    raw_varbinds = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True, null=True)
    received_at = models.DateTimeField(db_index=True)
    is_mapped = models.BooleanField(default=False)
    is_processed = models.BooleanField(default=False)

    class Meta:
        db_table = "snmp_trap_events"
        indexes = [models.Index(fields=["source_ip"]), models.Index(fields=["trap_oid"]), models.Index(fields=["device"]), models.Index(fields=["received_at"]), models.Index(fields=["is_mapped"]), models.Index(fields=["is_processed"])]

    def __str__(self):
        return f"{self.source_ip} {self.trap_oid}"
