from django.db import models
from apps.common.models import TimeStampedModel

class AuditAction(models.TextChoices):
    LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login Success"
    LOGIN_FAILED = "LOGIN_FAILED", "Login Failed"
    LOGOUT = "LOGOUT", "Logout"
    CREATE = "CREATE", "Create"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"
    ROLE_ASSIGNED = "ROLE_ASSIGNED", "Role Assigned"
    ROLE_REMOVED = "ROLE_REMOVED", "Role Removed"
    ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED", "Alert Acknowledged"
    ALERT_RESOLVED = "ALERT_RESOLVED", "Alert Resolved"
    ALERT_SUPPRESSED = "ALERT_SUPPRESSED", "Alert Suppressed"
    ALERT_ESCALATED = "ALERT_ESCALATED", "Alert Escalated"
    REPORT_GENERATION_REQUESTED = "REPORT_GENERATION_REQUESTED", "Report Generation Requested"
    REPORT_RETRY_REQUESTED = "REPORT_RETRY_REQUESTED", "Report Retry Requested"
    REPORT_CANCELLED = "REPORT_CANCELLED", "Report Cancelled"
    REPORT_GENERATION_STARTED = "REPORT_GENERATION_STARTED", "Report Generation Started"
    REPORT_GENERATED = "REPORT_GENERATED", "Report Generated"
    REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED", "Report Generation Failed"
    REPORT_DOWNLOADED = "REPORT_DOWNLOADED", "Report Downloaded"
    CREDENTIAL_UPDATED = "CREDENTIAL_UPDATED", "Credential Updated"

class AuditLog(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.SET_NULL, related_name="audit_logs", blank=True, null=True)
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="audit_logs", blank=True, null=True)
    action = models.CharField(max_length=100, choices=AuditAction.choices, db_index=True)
    resource_type = models.CharField(max_length=100, blank=True, null=True)
    resource_id = models.CharField(max_length=100, blank=True, null=True)
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "audit_logs"
        indexes = [models.Index(fields=["organization"]), models.Index(fields=["actor"]), models.Index(fields=["action"]), models.Index(fields=["resource_type", "resource_id"]), models.Index(fields=["created_at"])]
    def __str__(self): return f"{self.action} by {self.actor}"
