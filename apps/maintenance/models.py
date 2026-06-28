from django.db import models
from apps.common.models import TimeStampedModel

class MaintenanceType(models.TextChoices):
    PREVENTIVE = "PREVENTIVE", "Preventive"
    CORRECTIVE = "CORRECTIVE", "Corrective"
    INSPECTION = "INSPECTION", "Inspection"
    EMERGENCY = "EMERGENCY", "Emergency"

class MaintenanceStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"

class MaintenanceTicket(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="maintenance_tickets")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="maintenance_tickets")
    device = models.ForeignKey("devices.Device", on_delete=models.SET_NULL, related_name="maintenance_tickets", blank=True, null=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    maintenance_type = models.CharField(max_length=30, choices=MaintenanceType.choices)
    status = models.CharField(max_length=30, choices=MaintenanceStatus.choices, default=MaintenanceStatus.SCHEDULED)
    scheduled_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    assigned_to = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="assigned_maintenance_tickets", blank=True, null=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="created_maintenance_tickets", blank=True, null=True)
    class Meta:
        db_table = "maintenance_tickets"
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["device"]), models.Index(fields=["status"]), models.Index(fields=["scheduled_at"])]
    def __str__(self): return self.title
