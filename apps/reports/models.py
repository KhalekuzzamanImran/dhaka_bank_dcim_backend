from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel
from .constants import (
    REPORT_SCHEDULE_FORMAT_CHOICES,
    REPORT_SCHEDULE_FREQUENCY_CHOICES,
    REPORT_TYPE_CHOICES,
    normalize_report_format,
    normalize_report_frequency,
    normalize_report_type,
)

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


class ReportSchedule(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_schedules")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="report_schedules", blank=True, null=True)
    name = models.CharField(max_length=255)
    report_type = models.CharField(max_length=100, choices=REPORT_TYPE_CHOICES)
    frequency = models.CharField(max_length=30, choices=REPORT_SCHEDULE_FREQUENCY_CHOICES, default="DAILY")
    delivery_time = models.TimeField(default=time(6, 0))
    output_format = models.CharField(max_length=30, choices=REPORT_SCHEDULE_FORMAT_CHOICES, default="PDF_CSV")
    recipients = models.JSONField(default=list, blank=True)
    attach_raw_data = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    last_delivery_status = models.CharField(max_length=30, blank=True, null=True, default="PENDING")
    last_error_message = models.TextField(blank=True, null=True)
    last_job = models.ForeignKey("ReportJob", on_delete=models.SET_NULL, blank=True, null=True, related_name="+")
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="created_report_schedules")

    class Meta:
        db_table = "report_schedules"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["report_type"]),
            models.Index(fields=["frequency"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["next_run_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.name

    @staticmethod
    def _make_aware(value: datetime) -> datetime:
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    @staticmethod
    def _add_months(value: datetime, months: int) -> datetime:
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    def _shift_by_frequency(self, value: datetime, steps: int = 1) -> datetime:
        frequency = self.frequency
        if frequency == "DAILY":
            return value + timedelta(days=steps)
        if frequency == "WEEKLY":
            return value + timedelta(weeks=steps)
        if frequency == "MONTHLY":
            return self._add_months(value, steps)
        if frequency == "QUARTERLY":
            return self._add_months(value, steps * 3)
        return value + timedelta(days=steps)

    def normalize_recipients(self) -> list[str]:
        recipients = self.recipients if isinstance(self.recipients, list) else []
        normalized = []
        for recipient in recipients:
            candidate = str(recipient).strip().lower()
            if not candidate:
                continue
            normalized.append(candidate)
        seen = set()
        unique = []
        for recipient in normalized:
            if recipient in seen:
                continue
            seen.add(recipient)
            unique.append(recipient)
        return unique

    def calculate_next_run_at(self, reference_time=None):
        reference_time = reference_time or timezone.now()
        reference_time = self._make_aware(reference_time)
        run_time = datetime.combine(reference_time.date(), self.delivery_time)
        run_time = self._make_aware(run_time)
        if run_time <= reference_time:
            run_time = self._shift_by_frequency(run_time, 1)
        return run_time

    def calculate_execution_window(self, reference_time=None):
        end_time = reference_time or timezone.now()
        end_time = self._make_aware(end_time)
        if self.last_run_at:
            start_time = self.last_run_at
        else:
            start_time = self._shift_by_frequency(end_time, -1)
        return self._make_aware(start_time), end_time

    @property
    def report_type_label(self):
        for code, label in REPORT_TYPE_CHOICES:
            if code == self.report_type:
                return label
        return self.report_type

    def clean(self):
        super().clean()

        errors = {}
        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")

        report_type = normalize_report_type(self.report_type)
        if not report_type:
            errors.setdefault("report_type", []).append("Unsupported report type.")
        else:
            self.report_type = report_type

        frequency = normalize_report_frequency(self.frequency)
        if not frequency:
            errors.setdefault("frequency", []).append("Unsupported frequency.")
        else:
            self.frequency = frequency

        output_format = normalize_report_format(self.output_format)
        if not output_format:
            errors.setdefault("output_format", []).append("Unsupported report format.")
        else:
            self.output_format = output_format

        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if not isinstance(self.recipients, list):
            errors.setdefault("recipients", []).append("Recipients must be a list of email addresses.")
        else:
            normalized_recipients = self.normalize_recipients()
            if not normalized_recipients:
                errors.setdefault("recipients", []).append("At least one recipient email is required.")
            for recipient in normalized_recipients:
                if recipient.count("@") != 1:
                    errors.setdefault("recipients", []).append(f"Invalid recipient email: {recipient}")
                    continue
                local_part, domain_part = recipient.split("@", 1)
                if not local_part or not domain_part:
                    errors.setdefault("recipients", []).append(f"Invalid recipient email: {recipient}")
            self.recipients = normalized_recipients

        if self.is_active and not self.next_run_at:
            self.next_run_at = self.calculate_next_run_at()

        if self.last_run_at and self.next_run_at and self.last_run_at > self.next_run_at:
            errors.setdefault("next_run_at", []).append("Next run time must be after the last run time.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self._state.adding and not self.next_run_at:
            self.next_run_at = self.calculate_next_run_at()
        self.full_clean()
        return super().save(*args, **kwargs)
