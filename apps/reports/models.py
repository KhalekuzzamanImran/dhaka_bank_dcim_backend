from django.core.exceptions import ValidationError
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

    def __str__(self):
        return self.name

    @property
    def report_type(self):
        if isinstance(self.config, dict):
            return self.config.get("report_type")
        return None

    def clean(self):
        super().clean()

        errors = {}
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")
        if not self.code:
            errors.setdefault("code", []).append("Code is required.")
        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")
        if not isinstance(self.config, dict):
            errors.setdefault("config", []).append("Config must be a dictionary/object.")
        else:
            report_type = self.config.get("report_type")
            if not report_type:
                errors.setdefault("config", []).append("Config must include report_type.")

        if self.is_active and self.organization_id and self.code:
            duplicate_qs = ReportTemplate.objects.filter(
                organization_id=self.organization_id,
                code=self.code,
                is_active=True,
            )
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                errors.setdefault("__all__", []).append(
                    "An active report template with the same organization and code already exists."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

class ReportJobStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"

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

    def __str__(self):
        return f"ReportJob {self.id}"

    @property
    def report_type(self):
        if self.template_id and isinstance(self.template.config, dict):
            return self.template.config.get("report_type")
        if isinstance(self.parameters, dict):
            return self.parameters.get("report_type")
        return None

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.completed_at or None
        if end is None:
            from django.utils import timezone

            end = timezone.now()
        return max(0, int((end - self.started_at).total_seconds()))

    @property
    def is_downloadable(self):
        return self.status == ReportJobStatus.COMPLETED and bool(self.file)

    @property
    def can_retry(self):
        return self.status == ReportJobStatus.FAILED

    @property
    def can_cancel(self):
        return self.status == ReportJobStatus.PENDING

    def clean(self):
        super().clean()

        errors = {}

        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")

        if not isinstance(self.parameters, dict):
            errors.setdefault("parameters", []).append("Parameters must be a dictionary/object.")

        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if self.template_id and self.organization_id and self.template.organization_id != self.organization_id:
            errors.setdefault("template", []).append("Template must belong to the selected organization.")

        if self._state.adding and self.requested_by_id is None:
            errors.setdefault("requested_by", []).append("Requested by is required for user-created jobs.")

        if self.started_at and self.completed_at and self.started_at > self.completed_at:
            errors.setdefault("started_at", []).append("Started time cannot be after completed time.")
            errors.setdefault("completed_at", []).append("Completed time cannot be before started time.")

        if self.status == ReportJobStatus.PENDING:
            if self.started_at is not None:
                errors.setdefault("started_at", []).append("Pending jobs cannot have a started time.")
            if self.completed_at is not None:
                errors.setdefault("completed_at", []).append("Pending jobs cannot have a completed time.")

        if self.status == ReportJobStatus.PROCESSING:
            if self.started_at is None:
                errors.setdefault("started_at", []).append("Processing jobs must have a started time.")
            if self.completed_at is not None:
                errors.setdefault("completed_at", []).append("Processing jobs cannot have a completed time.")

        if self.status in {ReportJobStatus.COMPLETED, ReportJobStatus.FAILED, ReportJobStatus.CANCELLED}:
            if self.started_at is None:
                errors.setdefault("started_at", []).append("Completed jobs must have a started time.")
            if self.completed_at is None:
                errors.setdefault("completed_at", []).append("Terminal jobs must have a completed time.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
