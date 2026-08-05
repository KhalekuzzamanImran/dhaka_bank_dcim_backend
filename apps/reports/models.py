from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel
from apps.notifications.models import NotificationChannel, NotificationStatus
from .enums import (
    ReportArtifactFormat,
    ReportDefinitionCategory,
    ReportDeliveryStatus,
    ReportJobStatusV2,
    ReportRecipientChannel,
    ReportScheduleStatus,
    ReportTriggerSource,
)
from .constants import (
    REPORT_SCHEDULE_FORMAT_CHOICES,
    REPORT_SCHEDULE_FREQUENCY_CHOICES,
    REPORT_TYPE_CHOICES,
    normalize_report_format,
    normalize_report_frequency,
)
from .services.configuration import validate_report_template_config


def _normalize_string_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]
    normalized: list[str] = []
    seen = set()
    for item in iterable:
        candidate = str(item).strip()
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_optional_text(value):
    if value in (None, ""):
        return None
    candidate = str(value).strip()
    return candidate or None


def _normalize_json_object(value, field_name: str):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError({field_name: [f"{field_name.replace('_', ' ').title()} must be a dictionary/object."]})
    return value


class ReportDefinition(TimeStampedModel):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=32, choices=ReportDefinitionCategory.choices)
    generator_key = models.CharField(max_length=100, unique=True)
    parameter_schema = models.JSONField(default=dict, blank=True)
    supported_formats = models.JSONField(default=list, blank=True)
    supported_delivery_channels = models.JSONField(default=list, blank=True)
    requires_telemetry = models.BooleanField(default=False)
    requires_data_center = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "report_definitions"
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["generator_key"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()

        errors = {}
        if not self.code:
            errors.setdefault("code", []).append("Code is required.")
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")
        if not self.generator_key:
            errors.setdefault("generator_key", []).append("Generator key is required.")
        if not isinstance(self.parameter_schema, dict):
            errors.setdefault("parameter_schema", []).append("Parameter schema must be a dictionary/object.")
        if not isinstance(self.supported_formats, list):
            errors.setdefault("supported_formats", []).append("Supported formats must be a list.")
        if not isinstance(self.supported_delivery_channels, list):
            errors.setdefault("supported_delivery_channels", []).append("Supported delivery channels must be a list.")
        if self.version < 1:
            errors.setdefault("version", []).append("Version must be greater than zero.")

        self.supported_formats = [str(value).strip().upper() for value in _normalize_string_list(self.supported_formats)]
        self.supported_delivery_channels = [str(value).strip().upper() for value in _normalize_string_list(self.supported_delivery_channels)]

        allowed_formats = set(ReportArtifactFormat.values)
        invalid_formats = [value for value in self.supported_formats if value not in allowed_formats]
        if invalid_formats:
            errors.setdefault("supported_formats", []).append(
                f"Unsupported report artifact format(s): {', '.join(invalid_formats)}."
            )

        allowed_channels = set(NotificationChannel.values)
        invalid_channels = [value for value in self.supported_delivery_channels if value not in allowed_channels]
        if invalid_channels:
            errors.setdefault("supported_delivery_channels", []).append(
                f"Unsupported delivery channel(s): {', '.join(invalid_channels)}."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

class ReportTemplate(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_templates")
    definition = models.ForeignKey(ReportDefinition, on_delete=models.SET_NULL, blank=True, null=True, related_name="templates")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    config = models.JSONField(default=dict, blank=True)
    default_parameters = models.JSONField(default=dict, blank=True)
    primary_format = models.CharField(max_length=30, blank=True, null=True)
    attachment_formats = models.JSONField(default=list, blank=True)
    include_charts = models.BooleanField(default=False)
    include_raw_data = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="updated_report_templates")
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "report_templates"
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uq_org_report_template_code")]
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["definition"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()

        errors = {}
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")
        if not self.code:
            errors.setdefault("code", []).append("Code is required.")
        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")
        if isinstance(self.config, dict):
            try:
                self.config = validate_report_template_config(
                    self.config,
                    existing_config={},
                    definition_code=getattr(self.definition, "code", None) if self.definition_id else None,
                )
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field_name, messages in exc.message_dict.items():
                        errors.setdefault(field_name, []).extend(messages)
                else:
                    errors.setdefault("config", []).extend(exc.messages)
        else:
            errors.setdefault("config", []).append("Config must be a dictionary/object.")

        if self.definition_id and self.definition and not self.definition.is_active:
            errors.setdefault("definition", []).append("Report definition must be active.")

        if not isinstance(self.default_parameters, dict):
            errors.setdefault("default_parameters", []).append("Default parameters must be a dictionary/object.")
        if not isinstance(self.attachment_formats, list):
            errors.setdefault("attachment_formats", []).append("Attachment formats must be a list.")
        self.attachment_formats = [str(value).strip().upper() for value in _normalize_string_list(self.attachment_formats)]
        self.primary_format = _normalize_optional_text(self.primary_format)
        if self.primary_format:
            self.primary_format = self.primary_format.upper()
        if self.version < 1:
            errors.setdefault("version", []).append("Version must be greater than zero.")

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
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    PROCESSING = "PROCESSING", "Processing"
    PARTIAL = "PARTIAL", "Partial"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"

class ReportJob(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_jobs")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="report_jobs", blank=True, null=True)
    definition = models.ForeignKey(ReportDefinition, on_delete=models.SET_NULL, blank=True, null=True, related_name="jobs")
    template = models.ForeignKey(ReportTemplate, on_delete=models.SET_NULL, related_name="report_jobs", blank=True, null=True)
    schedule = models.ForeignKey("ReportSchedule", on_delete=models.SET_NULL, blank=True, null=True, related_name="jobs")
    requested_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, related_name="report_jobs", blank=True, null=True)
    status = models.CharField(max_length=30, choices=ReportJobStatus.choices, default=ReportJobStatus.PENDING)
    parameters = models.JSONField(default=dict, blank=True)
    output_config_snapshot = models.JSONField(default=dict, blank=True)
    parameters_snapshot = models.JSONField(default=dict, blank=True)
    template_snapshot = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to="reports/", blank=True, null=True, max_length=500)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True, default="")
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    queued_at = models.DateTimeField(default=timezone.now)
    retry_count = models.PositiveIntegerField(default=0)
    recipient_snapshot = models.JSONField(default=dict, blank=True)
    scope_snapshot = models.JSONField(default=dict, blank=True)
    template_config_snapshot = models.JSONField(default=dict, blank=True)
    source_event_snapshot = models.JSONField(default=dict, blank=True)
    trigger_source = models.CharField(max_length=32, choices=ReportTriggerSource.choices, default=ReportTriggerSource.MANUAL)
    idempotency_key = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    class Meta:
        db_table = "report_jobs"
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["data_center", "created_at"]),
            models.Index(fields=["definition"]),
            models.Index(fields=["template"]),
            models.Index(fields=["schedule"]),
            models.Index(fields=["requested_by"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(idempotency_key__isnull=True) | ~models.Q(idempotency_key=""),
                name="report_job_idempotency_key_not_blank",
            ),
            models.UniqueConstraint(
                fields=["organization", "requested_by", "trigger_source", "idempotency_key"],
                condition=models.Q(trigger_source=ReportTriggerSource.MANUAL, requested_by__isnull=False, idempotency_key__isnull=False),
                name="uq_report_job_manual_idempotency",
            ),
            models.UniqueConstraint(
                fields=["organization", "definition", "trigger_source", "idempotency_key"],
                condition=models.Q(trigger_source=ReportTriggerSource.EVENT, definition__isnull=False, idempotency_key__isnull=False),
                name="uq_report_job_event_definition_idempotency",
            ),
        ]

    def __str__(self):
        return f"ReportJob {self.id}"

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
        return self.status == ReportJobStatus.COMPLETED and self.artifacts.exists()

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
        if not isinstance(self.parameters_snapshot, dict):
            errors.setdefault("parameters_snapshot", []).append("Parameters snapshot must be a dictionary/object.")
        if not isinstance(self.template_snapshot, dict):
            errors.setdefault("template_snapshot", []).append("Template snapshot must be a dictionary/object.")
        if not isinstance(self.output_config_snapshot, dict):
            errors.setdefault("output_config_snapshot", []).append("Output config snapshot must be a dictionary/object.")
        if not isinstance(self.recipient_snapshot, dict):
            errors.setdefault("recipient_snapshot", []).append("Recipient snapshot must be a dictionary/object.")
        if not isinstance(self.scope_snapshot, dict):
            errors.setdefault("scope_snapshot", []).append("Scope snapshot must be a dictionary/object.")
        if not isinstance(self.template_config_snapshot, dict):
            errors.setdefault("template_config_snapshot", []).append("Template config snapshot must be a dictionary/object.")
        if not isinstance(self.source_event_snapshot, dict):
            errors.setdefault("source_event_snapshot", []).append("Source event snapshot must be a dictionary/object.")

        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if self.definition_id and self.template_id and self.template.definition_id:
            if self.template.definition_id != self.definition_id:
                errors.setdefault("definition", []).append("Definition must match the selected template.")

        if self.template_id and self.organization_id and self.template.organization_id != self.organization_id:
            errors.setdefault("template", []).append("Template must belong to the selected organization.")

        if self.schedule_id and self.organization_id and self.schedule.organization_id != self.organization_id:
            errors.setdefault("schedule", []).append("Schedule must belong to the selected organization.")

        if self.trigger_source == ReportTriggerSource.SCHEDULED and self.schedule_id is None:
            errors.setdefault("schedule", []).append("Scheduled jobs must reference a schedule.")
        if self.trigger_source == ReportTriggerSource.EVENT and self.definition_id is None:
            errors.setdefault("definition", []).append("Event jobs must reference a report definition.")

        if self._state.adding and self.requested_by_id is None and self.trigger_source != ReportTriggerSource.EVENT:
            errors.setdefault("requested_by", []).append("Requested by is required for user-created and scheduled jobs.")

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

        if self.status == ReportJobStatus.FAILED and self.failed_at is None:
            errors.setdefault("failed_at", []).append("Failed jobs must have a failed time.")
        if self.status == ReportJobStatus.CANCELLED and self.cancelled_at is None:
            errors.setdefault("cancelled_at", []).append("Cancelled jobs must have a cancelled time.")
        if self.progress_percent < 0 or self.progress_percent > 100:
            errors.setdefault("progress_percent", []).append("Progress percent must be between 0 and 100.")
        if self.idempotency_key is not None and not str(self.idempotency_key).strip():
            errors.setdefault("idempotency_key", []).append("Idempotency key cannot be blank.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportSchedule(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_schedules")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="report_schedules", blank=True, null=True)
    template = models.ForeignKey(ReportTemplate, on_delete=models.SET_NULL, blank=True, null=True, related_name="schedules")
    name = models.CharField(max_length=255)
    report_type = models.CharField(max_length=100, choices=REPORT_TYPE_CHOICES, blank=True, default="")
    frequency = models.CharField(max_length=30, choices=REPORT_SCHEDULE_FREQUENCY_CHOICES, default="DAILY")
    delivery_time = models.TimeField(default=time(6, 0))
    output_format = models.CharField(max_length=30, choices=REPORT_SCHEDULE_FORMAT_CHOICES, default="PDF")
    parameters = models.JSONField(default=dict, blank=True)
    parameter_overrides = models.JSONField(default=dict, blank=True)
    recipients = models.JSONField(default=list, blank=True)
    days_of_week = models.JSONField(default=list, blank=True)
    day_of_month = models.PositiveSmallIntegerField(blank=True, null=True)
    timezone = models.CharField(max_length=64, default="Asia/Dhaka")
    send_sms = models.BooleanField(default=False)
    sms_recipients = models.JSONField(default=list, blank=True)
    attachment_formats = models.JSONField(default=list, blank=True)
    consecutive_failure_count = models.PositiveIntegerField(default=0)
    end_at = models.DateTimeField(blank=True, null=True)
    last_success_at = models.DateTimeField(blank=True, null=True)
    primary_format = models.CharField(max_length=30, blank=True, null=True)
    recurrence_rule = models.JSONField(default=dict, blank=True)
    start_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=ReportScheduleStatus.choices, default=ReportScheduleStatus.ACTIVE)
    attach_raw_data = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    last_delivery_status = models.CharField(max_length=30, blank=True, null=True, default="PENDING")
    last_error_message = models.TextField(blank=True, null=True)
    last_job = models.ForeignKey("ReportJob", on_delete=models.SET_NULL, blank=True, null=True, related_name="+")
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="created_report_schedules")
    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="updated_report_schedules")

    class Meta:
        db_table = "report_schedules"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["data_center", "created_at"]),
            models.Index(fields=["report_type"]),
            models.Index(fields=["template"]),
            models.Index(fields=["frequency"]),
            models.Index(fields=["status"]),
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

    def _schedule_timezone(self):
        try:
            return ZoneInfo(self.timezone or "Asia/Dhaka")
        except Exception:
            return timezone.get_current_timezone()

    def clean_fields(self, exclude=None):
        if str(self.output_format or "").strip().upper() == "PDF_CSV":
            self.output_format = "PDF"
        super().clean_fields(exclude=exclude)

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

    def calculate_next_run_at(self, reference_time=None):
        reference_time = reference_time or timezone.now()
        schedule_tz = self._schedule_timezone()
        if timezone.is_naive(reference_time):
            reference_time = timezone.make_aware(reference_time, schedule_tz)
        else:
            reference_time = reference_time.astimezone(schedule_tz)
        run_time = datetime.combine(reference_time.date(), self.delivery_time, tzinfo=schedule_tz)
        if run_time <= reference_time:
            run_time = self._shift_by_frequency(run_time, 1)
        return run_time

    def calculate_execution_window(self, reference_time=None):
        schedule_tz = self._schedule_timezone()
        end_time = reference_time or timezone.now()
        if timezone.is_naive(end_time):
            end_time = timezone.make_aware(end_time, schedule_tz)
        else:
            end_time = end_time.astimezone(schedule_tz)
        if self.last_run_at:
            start_time = self.last_run_at
            if timezone.is_naive(start_time):
                start_time = timezone.make_aware(start_time, schedule_tz)
            else:
                start_time = start_time.astimezone(schedule_tz)
        else:
            start_time = self._shift_by_frequency(end_time, -1)
        return start_time, end_time

    def clean(self):
        super().clean()

        errors = {}
        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")

        self.timezone = str(self.timezone or "Asia/Dhaka").strip() or "Asia/Dhaka"
        try:
            ZoneInfo(self.timezone)
        except Exception:
            errors.setdefault("timezone", []).append("Unsupported timezone.")

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

        if self.template_id and self.organization_id and self.template.organization_id != self.organization_id:
            errors.setdefault("template", []).append("Template must belong to the selected organization.")

        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if self.parameters is None or not isinstance(self.parameters, dict):
            errors.setdefault("parameters", []).append("Parameters must be a dictionary/object.")
        if self.parameter_overrides is None or not isinstance(self.parameter_overrides, dict):
            errors.setdefault("parameter_overrides", []).append("Parameter overrides must be a dictionary/object.")
        if self.recurrence_rule is None or not isinstance(self.recurrence_rule, dict):
            errors.setdefault("recurrence_rule", []).append("Recurrence rule must be a dictionary/object.")
        if not isinstance(self.days_of_week, list):
            errors.setdefault("days_of_week", []).append("Days of week must be a list.")
        if self.day_of_month is not None and (self.day_of_month < 1 or self.day_of_month > 31):
            errors.setdefault("day_of_month", []).append("Day of month must be between 1 and 31.")
        if self.status not in ReportScheduleStatus.values:
            errors.setdefault("status", []).append("Unsupported schedule status.")
        if self.status == ReportScheduleStatus.ACTIVE and not self.next_run_at:
            self.next_run_at = self.calculate_next_run_at()

        if self.last_run_at and self.next_run_at and self.last_run_at > self.next_run_at:
            errors.setdefault("next_run_at", []).append("Next run time must be after the last run time.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self._state.adding and not self.next_run_at:
            self.next_run_at = self.calculate_next_run_at()
        elif self.pk:
            previous = type(self).objects.filter(pk=self.pk).values("delivery_time", "frequency", "status").first()
            if previous and (
                previous["delivery_time"] != self.delivery_time
                or previous["frequency"] != self.frequency
                or (self.status == ReportScheduleStatus.ACTIVE and previous["status"] != ReportScheduleStatus.ACTIVE)
            ):
                self.next_run_at = self.calculate_next_run_at()
                if update_fields is not None:
                    update_fields = set(update_fields)
                    update_fields.add("next_run_at")
                    kwargs["update_fields"] = update_fields
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportScheduleRunStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class ReportScheduleDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    DELIVERING = "DELIVERING", "Delivering"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"


class ReportScheduleRun(TimeStampedModel):
    schedule = models.ForeignKey(ReportSchedule, on_delete=models.CASCADE, related_name="runs")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="report_schedule_runs")
    requested_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="requested_report_schedule_runs")
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    scheduled_for = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=ReportScheduleRunStatus.choices, default=ReportScheduleRunStatus.PENDING)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    job = models.ForeignKey("ReportJob", on_delete=models.SET_NULL, blank=True, null=True, related_name="+")
    generated_job = models.ForeignKey("ReportJob", on_delete=models.SET_NULL, blank=True, null=True, related_name="+")
    error_message = models.TextField(blank=True, default="")
    trigger_source = models.CharField(max_length=32, choices=ReportTriggerSource.choices, default=ReportTriggerSource.SCHEDULED)
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "report_schedule_runs"
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "scheduled_for", "trigger_source"],
                condition=models.Q(trigger_source=ReportTriggerSource.SCHEDULED, scheduled_for__isnull=False),
                name="uq_report_schedule_run_scheduled_window",
            ),
        ]
        indexes = [
            models.Index(fields=["schedule"]),
            models.Index(fields=["status"]),
            models.Index(fields=["schedule", "created_at"]),
            models.Index(fields=["queued_at"]),
            models.Index(fields=["started_at"]),
            models.Index(fields=["completed_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"ReportScheduleRun {self.id}"

    def clean(self):
        super().clean()

        errors = {}
        if self.schedule_id and self.organization_id and self.schedule.organization_id != self.organization_id:
            errors.setdefault("organization", []).append("Organization must match the schedule organization.")
        if self.window_start and self.window_end and self.window_start > self.window_end:
            errors.setdefault("window_start", []).append("Window start cannot be after window end.")
            errors.setdefault("window_end", []).append("Window end cannot be before window start.")
        if self.scheduled_for and self.window_end and self.scheduled_for > self.window_end:
            errors.setdefault("scheduled_for", []).append("Scheduled for must not be after the execution window end.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportScheduleDelivery(TimeStampedModel):
    run = models.ForeignKey(ReportScheduleRun, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=30, choices=NotificationChannel.choices)
    status = models.CharField(max_length=30, choices=ReportScheduleDeliveryStatus.choices, default=ReportScheduleDeliveryStatus.PENDING, db_index=True)
    recipient_address = models.CharField(max_length=255, blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    queued_at = models.DateTimeField(blank=True, null=True)
    delivering_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    next_retry_at = models.DateTimeField(blank=True, null=True)
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    provider_response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "report_schedule_deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["run", "channel", "recipient_address"],
                name="unique_report_schedule_delivery_target",
            ),
        ]
        indexes = [
            models.Index(fields=["run"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["status"]),
            models.Index(fields=["next_retry_at"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["sent_at"]),
        ]

    def __str__(self):
        return f"{self.run_id}:{self.channel}:{self.recipient_address or 'default'}"


class ReportScheduleRecipient(TimeStampedModel):
    schedule = models.ForeignKey(ReportSchedule, on_delete=models.CASCADE, related_name="structured_recipients")
    channel = models.CharField(max_length=30, choices=ReportRecipientChannel.choices)
    recipient_type = models.CharField(max_length=30, blank=True, default="")
    destination = models.CharField(max_length=255, blank=True, default="")
    display_name = models.CharField(max_length=255, blank=True, default="")
    email_address = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=32, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "report_schedule_recipients"
        indexes = [
            models.Index(fields=["schedule"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(channel=ReportRecipientChannel.EMAIL, email_address__isnull=False) & ~models.Q(email_address=""))
                    | (models.Q(channel=ReportRecipientChannel.SMS, phone_number__isnull=False) & ~models.Q(phone_number=""))
                ),
                name="report_schedule_recipient_channel_contact_check",
            ),
        ]

    def __str__(self):
        contact = self.email_address or self.phone_number or self.display_name or self.channel
        return f"{self.schedule_id}:{self.channel}:{contact}"

    def clean(self):
        super().clean()

        errors = {}
        self.display_name = _normalize_optional_text(self.display_name) or ""
        self.email_address = _normalize_optional_text(self.email_address)
        self.phone_number = _normalize_optional_text(self.phone_number)
        self.recipient_type = (self.recipient_type or self.channel or "").strip().upper()

        if self.channel == ReportRecipientChannel.EMAIL:
            if not self.email_address:
                errors.setdefault("email_address", []).append("Email recipients require an email address.")
            if self.phone_number:
                self.phone_number = None
            self.destination = self.email_address or ""
        elif self.channel == ReportRecipientChannel.SMS:
            if not self.phone_number:
                errors.setdefault("phone_number", []).append("SMS recipients require a phone number.")
            if self.email_address:
                self.email_address = None
            self.destination = self.phone_number or ""
        else:
            errors.setdefault("channel", []).append("Unsupported recipient channel.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportArtifact(TimeStampedModel):
    job = models.ForeignKey(ReportJob, on_delete=models.CASCADE, related_name="artifacts")
    artifact_type = models.CharField(max_length=30, default="PRIMARY")
    format = models.CharField(max_length=30, choices=ReportArtifactFormat.choices)
    file = models.FileField(upload_to="reports/artifacts/", max_length=500)
    file_name = models.CharField(max_length=255, blank=True, null=True)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=30, default="AVAILABLE")
    expires_at = models.DateTimeField(blank=True, null=True)
    retention_expires_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "report_artifacts"
        constraints = [
            models.UniqueConstraint(fields=["job", "format"], name="uq_report_artifact_job_format"),
        ]
        indexes = [
            models.Index(fields=["job"]),
            models.Index(fields=["format"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.job_id}:{self.format}:{self.original_filename}"

    def clean(self):
        super().clean()

        errors = {}
        if not self.job_id:
            errors.setdefault("job", []).append("Job is required.")
        if not self.artifact_type:
            errors.setdefault("artifact_type", []).append("Artifact type is required.")
        if not self.original_filename:
            errors.setdefault("original_filename", []).append("Original filename is required.")
        if self.size_bytes < 0:
            errors.setdefault("size_bytes", []).append("Size must be non-negative.")
        if self.checksum_sha256:
            checksum = self.checksum_sha256.strip().lower()
            if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
                errors.setdefault("checksum_sha256", []).append("Checksum must be a SHA-256 hex digest.")
            self.checksum_sha256 = checksum
        if not self.status:
            errors.setdefault("status", []).append("Status is required.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportDelivery(TimeStampedModel):
    job = models.ForeignKey(ReportJob, on_delete=models.CASCADE, related_name="deliveries")
    schedule_recipient = models.ForeignKey(ReportScheduleRecipient, on_delete=models.SET_NULL, blank=True, null=True, related_name="deliveries")
    channel = models.CharField(max_length=30, choices=NotificationChannel.choices)
    recipient_type = models.CharField(max_length=30, blank=True, default="")
    destination_snapshot = models.CharField(max_length=255, blank=True, default="")
    attempt_number = models.PositiveIntegerField(default=0)
    recipient = models.CharField(max_length=255)
    status = models.CharField(max_length=30, choices=ReportDeliveryStatus.choices, default=ReportDeliveryStatus.PENDING, db_index=True)
    queued_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    provider_response = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "report_deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "channel", "recipient"],
                name="uq_report_delivery_job_channel_recipient",
            ),
        ]
        indexes = [
            models.Index(fields=["job"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["sent_at"]),
        ]

    def __str__(self):
        return f"{self.job_id}:{self.channel}:{self.recipient}"

    def clean(self):
        super().clean()

        errors = {}
        if not self.job_id:
            errors.setdefault("job", []).append("Job is required.")
        if not self.recipient:
            errors.setdefault("recipient", []).append("Recipient is required.")
        if not self.channel:
            errors.setdefault("channel", []).append("Channel is required.")
        if self.retry_count < 0:
            errors.setdefault("retry_count", []).append("Retry count cannot be negative.")
        if not isinstance(self.provider_response, dict):
            errors.setdefault("provider_response", []).append("Provider response must be a dictionary/object.")
        if self.status == ReportDeliveryStatus.SENT and self.sent_at is None:
            errors.setdefault("sent_at", []).append("Sent deliveries must have a sent time.")
        if self.status == ReportDeliveryStatus.FAILED and self.failed_at is None:
            errors.setdefault("failed_at", []).append("Failed deliveries must have a failed time.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
