from django.db import models
from apps.common.models import TimeStampedModel

class ReportTemplate(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_templates")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "report_templates"
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uq_org_report_template_code")]
    def __str__(self): return self.name

class ReportJobStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"

class ReportJob(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_jobs")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="report_jobs", blank=True, null=True)
    template = models.ForeignKey(ReportTemplate, on_delete=models.SET_NULL, related_name="report_jobs", blank=True, null=True)
    requested_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="report_jobs", blank=True, null=True)
    status = models.CharField(max_length=30, choices=ReportJobStatus.choices, default=ReportJobStatus.PENDING)
    parameters = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to="reports/", blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "report_jobs"
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["requested_by"]), models.Index(fields=["status"]), models.Index(fields=["created_at"])]
    def __str__(self): return f"ReportJob {self.id}"
