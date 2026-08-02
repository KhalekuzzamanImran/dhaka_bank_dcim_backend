from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.access import get_access_scope
from apps.common.models import TimeStampedModel

from .constants import (
    REPORT_SCHEDULE_FORMAT_CHOICES,
    REPORT_SCHEDULE_FREQUENCY_CHOICES,
    REPORT_TYPE_CHOICES,
    REPORT_TYPE_LABELS,
    normalize_report_format,
    normalize_report_frequency,
    normalize_report_type,
)
from .domain import (
    REPORT_DEFINITION_SEEDS,
    ReportArtifactStatus,
    ReportArtifactType,
    ReportCategory,
    ReportDeliveryStatus,
    ReportJobStatus,
    ReportJobTriggerSource,
    ReportRecipientChannel,
    ReportRecipientType,
    ReportScheduleStatus,
    canonical_report_definition_code,
)


REPORT_OUTPUT_FORMAT_CHOICES = (
    ("PDF", "PDF"),
    ("CSV", "CSV"),
    ("XLSX", "XLSX"),
)
def _normalize_dict(value, default=None):
    if isinstance(value, dict):
        return value
    return {} if default is None else default


def _normalize_str_list(value):
    if not isinstance(value, list):
        return []
    normalized = []
    seen = set()
    for item in value:
        candidate = str(item).strip()
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)
    return normalized


def _normalize_email_list(value):
    normalized = []
    for recipient in _normalize_str_list(value):
        candidate = recipient.strip().lower()
        if candidate.count("@") != 1:
            raise ValidationError({"recipients": f"Invalid recipient email: {recipient}"})
        local_part, domain_part = candidate.split("@", 1)
        if not local_part or not domain_part:
            raise ValidationError({"recipients": f"Invalid recipient email: {recipient}"})
        normalized.append(candidate)
    return list(dict.fromkeys(normalized))


def _normalize_phone_list(value):
    normalized = []
    seen = set()
    for recipient in _normalize_str_list(value):
        candidate = recipient.strip()
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)
    return normalized


def _timezone_for_name(name: str | None):
    tz_name = name or getattr(settings, "TIME_ZONE", "UTC")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.get_current_timezone()


def _time_to_text(value: time | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%H:%M:%S")


def _parse_time_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
            try:
                return datetime.strptime(candidate, fmt).time()
            except ValueError:
                continue
    return None


def _combine_local(date_value: date, time_value: time, tz_name: str | None):
    tz = _timezone_for_name(tz_name)
    candidate = datetime.combine(date_value, time_value)
    if timezone.is_naive(candidate):
        return timezone.make_aware(candidate, tz)
    return candidate.astimezone(tz)


def _month_day(year: int, month: int, day: int, policy: str = "LAST_DAY") -> int:
    last_day = calendar.monthrange(year, month)[1]
    if day <= last_day:
        return day
    if str(policy).upper() == "ERROR":
        raise ValidationError({"recurrence_rule": "The requested monthly day does not exist in the target month."})
    return last_day


def _normalize_supported_formats(value):
    normalized = []
    for item in _normalize_str_list(value):
        candidate = item.upper()
        if candidate not in {choice[0] for choice in REPORT_OUTPUT_FORMAT_CHOICES}:
            raise ValidationError({"supported_formats": f"Unsupported format: {item}"})
        normalized.append(candidate)
    return normalized


def _report_definition_seed_map():
    return {entry["code"]: entry for entry in REPORT_DEFINITION_SEEDS}


class ReportDefinition(TimeStampedModel):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=50, choices=ReportCategory.choices)
    handler_key = models.CharField(max_length=120)
    parameter_schema = models.JSONField(default=dict, blank=True)
    supported_formats = models.JSONField(default=list, blank=True)
    supports_scheduling = models.BooleanField(default=True)
    supports_raw_attachment = models.BooleanField(default=False)
    supports_charts = models.BooleanField(default=False)
    required_permission = models.CharField(max_length=120, blank=True, null=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "report_definitions"
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["category"]),
            models.Index(fields=["handler_key"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["version"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_report_definition_code"),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def supported_codes(cls) -> set[str]:
        return set(_report_definition_seed_map())

    def clean(self):
        super().clean()
        errors = {}

        canonical_code = canonical_report_definition_code(self.code)
        if not canonical_code:
            errors.setdefault("code", []).append("Unsupported report definition code.")
        elif canonical_code != self.code:
            self.code = canonical_code

        seeds = _report_definition_seed_map()
        seed = seeds.get(self.code)
        if not seed:
            errors.setdefault("code", []).append("Report definition code is not registered in the supported seed set.")
        else:
            expected_handler = seed.get("handler_key")
            if expected_handler and self.handler_key and self.handler_key != expected_handler:
                errors.setdefault("handler_key", []).append("Handler key does not match the registered definition seed.")

        if not self.name:
            errors.setdefault("name", []).append("Name is required.")
        if not self.handler_key:
            errors.setdefault("handler_key", []).append("Handler key is required.")
        if not isinstance(self.parameter_schema, dict):
            errors.setdefault("parameter_schema", []).append("Parameter schema must be a dictionary/object.")
        if not isinstance(self.supported_formats, list):
            errors.setdefault("supported_formats", []).append("Supported formats must be a list.")
        else:
            self.supported_formats = _normalize_supported_formats(self.supported_formats)
        if self.version < 1:
            errors.setdefault("version", []).append("Version must be greater than zero.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        canonical_code = canonical_report_definition_code(self.code)
        if canonical_code:
            self.code = canonical_code
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportTemplate(TimeStampedModel):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="report_templates",
        blank=True,
        null=True,
    )
    definition = models.ForeignKey(
        ReportDefinition,
        on_delete=models.SET_NULL,
        related_name="templates",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    config = models.JSONField(default=dict, blank=True)
    branding_config = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="created_report_templates",
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "report_templates"
        indexes = [
            models.Index(fields=["organization", "code"]),
            models.Index(fields=["definition"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["version"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code", "version"],
                condition=models.Q(organization__isnull=False),
                name="uq_report_template_org_code_version",
            ),
            models.UniqueConstraint(
                fields=["code", "version"],
                condition=models.Q(organization__isnull=True),
                name="uq_report_template_global_code_version",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def report_type(self):
        if self.definition_id:
            return self.definition.code
        if isinstance(self.config, dict):
            return canonical_report_definition_code(self.config.get("report_type")) or self.config.get("report_type")
        return None

    def clean(self):
        super().clean()

        errors = {}
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")
        if not self.code:
            errors.setdefault("code", []).append("Code is required.")
        if not isinstance(self.config, dict):
            errors.setdefault("config", []).append("Config must be a dictionary/object.")
        if not isinstance(self.branding_config, dict):
            errors.setdefault("branding_config", []).append("Branding config must be a dictionary/object.")
        if self.version < 1:
            errors.setdefault("version", []).append("Version must be greater than zero.")

        config_report_type = canonical_report_definition_code(
            self.config.get("report_type") if isinstance(self.config, dict) else None
        )
        if self.definition_id and config_report_type and config_report_type != self.definition.code:
            errors.setdefault("config", []).append("Config report_type must match the selected definition.")

        duplicate_qs = ReportTemplate.objects.filter(
            organization_id=self.organization_id,
            code=self.code,
            version=self.version,
        )
        if self.pk:
            duplicate_qs = duplicate_qs.exclude(pk=self.pk)
        if duplicate_qs.exists():
            errors.setdefault("__all__", []).append(
                "A report template with the same organization, code, and version already exists."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not isinstance(self.config, dict):
            self.config = {}
        if not isinstance(self.branding_config, dict):
            self.branding_config = {}

        resolved_code = canonical_report_definition_code(self.config.get("report_type"))
        if not self.definition_id and resolved_code:
            self.definition = ReportDefinition.objects.filter(code=resolved_code, is_active=True).order_by("-version").first()
        if self.definition_id and isinstance(self.config, dict):
            self.config = {**self.config, "report_type": self.definition.code}
        if self.definition_id and not self.name:
            self.name = self.definition.name
        if self.definition_id and not self.description:
            self.description = self.definition.description
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportSchedule(TimeStampedModel):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="report_schedules",
    )
    data_center = models.ForeignKey(
        "datacenters.DataCenter",
        on_delete=models.CASCADE,
        related_name="report_schedules",
        blank=True,
        null=True,
    )
    definition = models.ForeignKey(
        ReportDefinition,
        on_delete=models.SET_NULL,
        related_name="schedules",
        blank=True,
        null=True,
    )
    template = models.ForeignKey(
        ReportTemplate,
        on_delete=models.SET_NULL,
        related_name="schedules",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=255)
    report_type = models.CharField(max_length=100, choices=REPORT_TYPE_CHOICES, blank=True, null=True)
    frequency = models.CharField(max_length=30, choices=REPORT_SCHEDULE_FREQUENCY_CHOICES, default="DAILY")
    delivery_time = models.TimeField(default=time(6, 0))
    primary_format = models.CharField(max_length=30, choices=REPORT_OUTPUT_FORMAT_CHOICES, blank=True, null=True)
    attachment_formats = models.JSONField(default=list, blank=True)
    output_format = models.CharField(max_length=30, choices=REPORT_SCHEDULE_FORMAT_CHOICES, default="PDF_CSV")
    recurrence_rule = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30, choices=ReportScheduleStatus.choices, default=ReportScheduleStatus.ACTIVE)
    start_at = models.DateTimeField(blank=True, null=True)
    end_at = models.DateTimeField(blank=True, null=True)
    parameters = models.JSONField(default=dict, blank=True)
    recipients = models.JSONField(default=list, blank=True)
    send_sms = models.BooleanField(default=False)
    sms_recipients = models.JSONField(default=list, blank=True)
    attach_raw_data = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    last_success_at = models.DateTimeField(blank=True, null=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    consecutive_failure_count = models.PositiveIntegerField(default=0)
    last_delivery_status = models.CharField(max_length=30, blank=True, null=True, default="PENDING")
    last_error_message = models.TextField(blank=True, null=True)
    last_job = models.ForeignKey(
        "ReportJob",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_report_schedules",
    )

    class Meta:
        db_table = "report_schedules"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["definition"]),
            models.Index(fields=["template"]),
            models.Index(fields=["report_type"]),
            models.Index(fields=["frequency"]),
            models.Index(fields=["status"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["next_run_at"]),
            models.Index(fields=["last_run_at"]),
            models.Index(fields=["last_success_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.name

    @property
    def report_type_label(self):
        if self.definition_id:
            return self.definition.name
        return REPORT_TYPE_LABELS.get(self.report_type, self.report_type)

    def normalize_recipients(self) -> list[str]:
        return _normalize_email_list(self.recipients)

    def normalize_sms_recipients(self) -> list[str]:
        return _normalize_phone_list(self.sms_recipients)

    def _timezone_name(self):
        return getattr(settings, "TIME_ZONE", "UTC")

    def _frequency_rule(self):
        rule = _normalize_dict(self.recurrence_rule)
        if not rule:
            rule = {}
        rule.setdefault("frequency", self.frequency)
        rule.setdefault("delivery_time", _time_to_text(self.delivery_time))
        return rule

    def _shift_by_frequency(self, value: datetime, steps: int = 1) -> datetime:
        frequency = normalize_report_frequency(self.frequency) or self.frequency
        if frequency == "DAILY":
            return value + timedelta(days=steps)
        if frequency == "WEEKLY":
            return value + timedelta(weeks=steps)
        if frequency == "MONTHLY":
            month_index = value.month - 1 + steps
            year = value.year + month_index // 12
            month = month_index % 12 + 1
            day = _month_day(year, month, value.day)
            return value.replace(year=year, month=month, day=day)
        if frequency == "QUARTERLY":
            month_index = value.month - 1 + steps * 3
            year = value.year + month_index // 12
            month = month_index % 12 + 1
            day = _month_day(year, month, value.day)
            return value.replace(year=year, month=month, day=day)
        return value + timedelta(days=steps)

    def calculate_next_run_at(self, reference_time=None):
        reference_time = reference_time or timezone.now()
        tz_name = self._timezone_name()
        tz = _timezone_for_name(tz_name)
        if timezone.is_naive(reference_time):
            local_reference = timezone.make_aware(reference_time, tz)
        else:
            local_reference = reference_time.astimezone(tz)

        rule = self._frequency_rule()
        delivery_time = _parse_time_value(rule.get("delivery_time")) or self.delivery_time
        frequency = normalize_report_frequency(rule.get("frequency") or self.frequency) or self.frequency
        candidate = _combine_local(local_reference.date(), delivery_time, tz_name)

        if self.start_at:
            start_at_local = self.start_at.astimezone(tz) if timezone.is_aware(self.start_at) else timezone.make_aware(self.start_at, tz)
            if candidate < start_at_local:
                candidate = start_at_local

        if frequency == "WEEKLY" and rule.get("weekdays"):
            weekdays = {int(day) for day in _normalize_str_list(rule.get("weekdays")) if str(day).isdigit()}
            if not weekdays:
                weekdays = {int(day) for day in rule.get("weekdays", []) if str(day).isdigit()}
            candidate = None
            for offset in range(0, 14):
                probe_date = (local_reference.date() + timedelta(days=offset))
                if probe_date.weekday() not in weekdays:
                    continue
                probe = _combine_local(probe_date, delivery_time, tz_name)
                if probe > local_reference:
                    candidate = probe
                    break
            if candidate is None:
                candidate = _combine_local(local_reference.date() + timedelta(days=7), delivery_time, tz_name)
        elif frequency in {"MONTHLY", "QUARTERLY"}:
            day_of_month = rule.get("day_of_month")
            invalid_policy = str(rule.get("invalid_monthly_date_policy") or "LAST_DAY").upper()
            if day_of_month is None:
                day_of_month = local_reference.day
            try:
                day_of_month = int(day_of_month)
            except (TypeError, ValueError):
                day_of_month = local_reference.day

            step_months = 1 if frequency == "MONTHLY" else 3
            candidate = _combine_local(local_reference.date(), delivery_time, tz_name)
            if candidate <= local_reference:
                month_index = local_reference.month - 1 + step_months
                year = local_reference.year + month_index // 12
                month = month_index % 12 + 1
                day = _month_day(year, month, day_of_month, invalid_policy)
                candidate = _combine_local(date(year, month, day), delivery_time, tz_name)
            else:
                day = _month_day(candidate.year, candidate.month, day_of_month, invalid_policy)
                candidate = _combine_local(date(candidate.year, candidate.month, day), delivery_time, tz_name)
        else:
            if candidate <= local_reference:
                candidate = self._shift_by_frequency(candidate, 1)

        if self.end_at:
            end_at_local = self.end_at.astimezone(tz) if timezone.is_aware(self.end_at) else timezone.make_aware(self.end_at, tz)
            if candidate > end_at_local:
                return None
        return candidate

    def calculate_execution_window(self, reference_time=None):
        end_time = reference_time or timezone.now()
        end_time = timezone.make_aware(end_time, _timezone_for_name(self._timezone_name())) if timezone.is_naive(end_time) else end_time
        if self.last_run_at:
            start_time = self.last_run_at
        else:
            start_time = self._shift_by_frequency(end_time, -1)
        return start_time, end_time

    def _sync_legacy_fields(self):
        delivery_time = _parse_time_value(self.delivery_time)
        if delivery_time is not None:
            self.delivery_time = delivery_time

        if self.definition_id:
            self.report_type = self.definition.code
        elif self.report_type:
            canonical_type = canonical_report_definition_code(self.report_type)
            if canonical_type:
                self.report_type = canonical_type
                self.definition = ReportDefinition.objects.filter(code=canonical_type, is_active=True).first()

        if not isinstance(self.parameters, dict):
            self.parameters = {}
        if not isinstance(self.recipients, list):
            self.recipients = []
        if not isinstance(self.sms_recipients, list):
            self.sms_recipients = []
        if not isinstance(self.attachment_formats, list):
            self.attachment_formats = []
        if not isinstance(self.recurrence_rule, dict):
            self.recurrence_rule = {}

        self.recipients = _normalize_email_list(self.recipients)
        self.sms_recipients = _normalize_phone_list(self.sms_recipients)

        if not self.primary_format:
            if self.output_format == "PDF_CSV":
                self.primary_format = "PDF"
                if "CSV" not in self.attachment_formats:
                    self.attachment_formats = [*self.attachment_formats, "CSV"]
            elif self.output_format in {"PDF", "CSV"}:
                self.primary_format = self.output_format
            else:
                self.primary_format = "PDF"

        if self.primary_format and self.primary_format not in {choice[0] for choice in REPORT_OUTPUT_FORMAT_CHOICES}:
            self.primary_format = "PDF"

        self.attachment_formats = _normalize_supported_formats(self.attachment_formats)
        if self.attach_raw_data and "CSV" not in self.attachment_formats:
            self.attachment_formats = [*self.attachment_formats, "CSV"]
        if not self.attach_raw_data:
            self.attachment_formats = [fmt for fmt in self.attachment_formats if fmt != "CSV"]

        if not self.recurrence_rule:
            self.recurrence_rule = {
                "frequency": self.frequency,
                "delivery_time": _time_to_text(self.delivery_time),
            }
        else:
            self.recurrence_rule["frequency"] = self.frequency
            self.recurrence_rule["delivery_time"] = _time_to_text(self.delivery_time)
            self.recurrence_rule.pop("timezone", None)

        if self.status == ReportScheduleStatus.ACTIVE and not self.is_active:
            self.status = ReportScheduleStatus.PAUSED
        self.is_active = self.status == ReportScheduleStatus.ACTIVE

        if self.status == ReportScheduleStatus.EXPIRED:
            self.is_active = False

        if not self.next_run_at and self.status == ReportScheduleStatus.ACTIVE:
            self.next_run_at = self.calculate_next_run_at()

        if self.end_at and self.next_run_at and self.next_run_at > self.end_at:
            self.status = ReportScheduleStatus.EXPIRED
            self.is_active = False

    def clean(self):
        super().clean()

        errors = {}
        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")
        if not self.name:
            errors.setdefault("name", []).append("Name is required.")

        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")

        if self.definition_id and self.template_id and self.template.definition_id and self.template.definition_id != self.definition_id:
            errors.setdefault("template", []).append("Template definition must match the selected definition.")

        if self.template_id and self.organization_id and self.template.organization_id not in {None, self.organization_id}:
            errors.setdefault("template", []).append("Template must belong to the selected organization or be global.")

        if self.definition_id and self.report_type and self.definition.code != self.report_type:
            errors.setdefault("definition", []).append("Definition must match the selected legacy report type.")

        if self.report_type and normalize_report_type(self.report_type) is None:
            errors.setdefault("report_type", []).append("Unsupported report type.")

        normalized_frequency = normalize_report_frequency(self.frequency)
        if not normalized_frequency:
            errors.setdefault("frequency", []).append("Unsupported frequency.")
        else:
            self.frequency = normalized_frequency

        normalized_format = normalize_report_format(self.output_format)
        if not normalized_format:
            errors.setdefault("output_format", []).append("Unsupported report format.")
        else:
            self.output_format = normalized_format

        if not isinstance(self.recurrence_rule, dict):
            errors.setdefault("recurrence_rule", []).append("Recurrence rule must be a dictionary/object.")

        if not isinstance(self.parameters, dict):
            errors.setdefault("parameters", []).append("Parameters must be a dictionary/object.")

        try:
            self.recipients = _normalize_email_list(self.recipients)
        except ValidationError as exc:
            errors.update(exc.message_dict)

        if not isinstance(self.sms_recipients, list):
            errors.setdefault("sms_recipients", []).append("SMS recipients must be a list.")
        else:
            self.sms_recipients = _normalize_phone_list(self.sms_recipients)

        if self.send_sms and not self.sms_recipients:
            errors.setdefault("sms_recipients", []).append("At least one SMS recipient is required when SMS is enabled.")
        if not self.recipients and not (self.send_sms and self.sms_recipients):
            errors.setdefault("recipients", []).append("At least one recipient email is required.")

        if self.start_at and self.end_at and self.start_at > self.end_at:
            errors.setdefault("end_at", []).append("End time must be after the start time.")
        if self.last_run_at and self.next_run_at and self.last_run_at > self.next_run_at:
            errors.setdefault("next_run_at", []).append("Next run time must be after the last run time.")
        if self.last_success_at and self.last_run_at and self.last_success_at > self.last_run_at:
            errors.setdefault("last_success_at", []).append("Last successful run cannot be after the last run.")

        if self.status not in ReportScheduleStatus.values:
            errors.setdefault("status", []).append("Unsupported schedule status.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        previous = None
        update_fields = kwargs.get("update_fields")
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "delivery_time",
                "frequency",
                "status",
                "is_active",
                "recurrence_rule",
                "start_at",
                "end_at",
            ).first()
        if self.definition_id and not self.report_type:
            self.report_type = self.definition.code
        if not self.definition_id and self.report_type:
            canonical_type = canonical_report_definition_code(self.report_type)
            if canonical_type:
                self.definition = ReportDefinition.objects.filter(code=canonical_type, is_active=True).first()
                self.report_type = canonical_type

        self._sync_legacy_fields()
        if previous:
            schedule_fields_changed = any(
                previous.get(field) != getattr(self, field)
                for field in (
                    "delivery_time",
                    "frequency",
                    "status",
                    "is_active",
                    "recurrence_rule",
                    "start_at",
                    "end_at",
                )
            )
            if schedule_fields_changed and self.status == ReportScheduleStatus.ACTIVE:
                self.next_run_at = self.calculate_next_run_at()
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {"next_run_at", "recurrence_rule"}
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportScheduleRecipient(TimeStampedModel):
    schedule = models.ForeignKey(
        ReportSchedule,
        on_delete=models.CASCADE,
        related_name="recipient_entries",
    )
    channel = models.CharField(max_length=10, choices=ReportRecipientChannel.choices)
    recipient_type = models.CharField(max_length=10, choices=ReportRecipientType.choices, default=ReportRecipientType.TO)
    destination = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True, null=True)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="report_schedule_recipients",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "report_schedule_recipients"
        indexes = [
            models.Index(fields=["schedule", "channel"]),
            models.Index(fields=["schedule", "is_active"]),
            models.Index(fields=["destination"]),
            models.Index(fields=["user"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "channel", "recipient_type", "destination"],
                name="uq_report_schedule_recipient_destination",
            ),
        ]

    def __str__(self):
        return f"{self.schedule_id}:{self.channel}:{self.destination}"

    def clean(self):
        super().clean()
        errors = {}
        if self.schedule_id and self.schedule.organization_id is None:
            errors.setdefault("schedule", []).append("Schedule must belong to an organization.")

        if self.schedule_id and self.user_id:
            scope = get_access_scope(self.user)
            if not (scope["global_access"] or self.schedule.organization_id in scope["organization_ids"]):
                errors.setdefault("user", []).append("Recipient user does not have access to the schedule organization.")

        if self.channel == ReportRecipientChannel.EMAIL:
            candidate = str(self.destination or "").strip().lower()
            if candidate.count("@") != 1:
                errors.setdefault("destination", []).append("Invalid email address.")
            else:
                local_part, domain_part = candidate.split("@", 1)
                if not local_part or not domain_part:
                    errors.setdefault("destination", []).append("Invalid email address.")
                self.destination = candidate
        elif self.channel == ReportRecipientChannel.SMS:
            candidate = str(self.destination or "").strip()
            if not candidate:
                errors.setdefault("destination", []).append("Invalid SMS destination.")
            self.destination = candidate
        else:
            errors.setdefault("channel", []).append("Unsupported recipient channel.")

        if self.recipient_type not in ReportRecipientType.values:
            errors.setdefault("recipient_type", []).append("Unsupported recipient type.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportArtifact(TimeStampedModel):
    job = models.ForeignKey(
        "ReportJob",
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    artifact_type = models.CharField(max_length=20, choices=ReportArtifactType.choices, default=ReportArtifactType.PRIMARY)
    format = models.CharField(max_length=10, choices=REPORT_OUTPUT_FORMAT_CHOICES)
    file = models.FileField(upload_to="reports/artifacts/", blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True, null=True)
    content_type = models.CharField(max_length=120, blank=True, null=True)
    size_bytes = models.BigIntegerField(blank=True, null=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True, null=True)
    status = models.CharField(max_length=20, choices=ReportArtifactStatus.choices, default=ReportArtifactStatus.GENERATING)
    expires_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "report_artifacts"
        indexes = [
            models.Index(fields=["job", "artifact_type"]),
            models.Index(fields=["job", "status"]),
            models.Index(fields=["format"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.job_id}:{self.artifact_type}:{self.format}"

    def _sync_file_metadata(self):
        if self.file and not self.file_name:
            self.file_name = self.file.name.rsplit("/", 1)[-1]
        if self.file:
            try:
                if self.size_bytes is None:
                    self.size_bytes = int(self.file.size)
            except Exception:
                pass
            if not self.checksum_sha256:
                try:
                    digest = hashlib.sha256()
                    self.file.open("rb")
                    for chunk in self.file.chunks():
                        digest.update(chunk)
                    self.checksum_sha256 = digest.hexdigest()
                except Exception:
                    pass

    def clean(self):
        super().clean()
        errors = {}
        if self.job_id and self.job.organization_id is None:
            errors.setdefault("job", []).append("Job must belong to an organization.")
        if self.job_id and self.job.organization_id and self.job.definition_id and self.job.template_id:
            if self.job.definition_id != self.job.template.definition_id and self.job.template.definition_id:
                errors.setdefault("job", []).append("Artifact job must be attached to a consistent report job.")
        if self.artifact_type not in ReportArtifactType.values:
            errors.setdefault("artifact_type", []).append("Unsupported artifact type.")
        if self.status not in ReportArtifactStatus.values:
            errors.setdefault("status", []).append("Unsupported artifact status.")
        if self.expires_at and self.created_at and self.expires_at < self.created_at:
            errors.setdefault("expires_at", []).append("Expiry time must be after creation time.")
        if self.size_bytes is not None and self.size_bytes < 0:
            errors.setdefault("size_bytes", []).append("Size must be greater than or equal to zero.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._sync_file_metadata()
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportDelivery(TimeStampedModel):
    job = models.ForeignKey(
        "ReportJob",
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    schedule = models.ForeignKey(
        ReportSchedule,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="deliveries",
    )
    artifact = models.ForeignKey(
        ReportArtifact,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="deliveries",
    )
    channel = models.CharField(max_length=10, choices=ReportRecipientChannel.choices)
    recipient = models.ForeignKey(
        ReportScheduleRecipient,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="deliveries",
    )
    recipient_type = models.CharField(max_length=10, choices=ReportRecipientType.choices, default=ReportRecipientType.TO)
    destination_snapshot = models.CharField(max_length=255)
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=ReportDeliveryStatus.choices, default=ReportDeliveryStatus.PENDING)
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    provider_response = models.JSONField(default=dict, blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    attempted_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    error_code = models.CharField(max_length=80, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "report_deliveries"
        indexes = [
            models.Index(fields=["job", "channel"]),
            models.Index(fields=["job", "status"]),
            models.Index(fields=["schedule", "channel"]),
            models.Index(fields=["recipient", "attempt_number"]),
            models.Index(fields=["queued_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "recipient", "channel", "attempt_number"],
                name="uq_report_delivery_attempt",
            ),
        ]

    def __str__(self):
        return f"{self.job_id}:{self.channel}:{self.destination_snapshot}:{self.attempt_number}"

    def clean(self):
        super().clean()
        errors = {}
        if self.job_id and self.schedule_id and self.job.schedule_id and self.job.schedule_id != self.schedule_id:
            errors.setdefault("schedule", []).append("Delivery schedule must match the job schedule.")
        if self.recipient_id and self.schedule_id and self.recipient.schedule_id != self.schedule_id:
            errors.setdefault("recipient", []).append("Recipient must belong to the selected schedule.")
        if self.artifact_id and self.job_id and self.artifact.job_id != self.job_id:
            errors.setdefault("artifact", []).append("Artifact must belong to the selected job.")
        if self.channel not in ReportRecipientChannel.values:
            errors.setdefault("channel", []).append("Unsupported delivery channel.")
        if self.recipient_type not in ReportRecipientType.values:
            errors.setdefault("recipient_type", []).append("Unsupported recipient type.")
        if self.status not in ReportDeliveryStatus.values:
            errors.setdefault("status", []).append("Unsupported delivery status.")
        if self.attempt_number < 1:
            errors.setdefault("attempt_number", []).append("Attempt number must be greater than zero.")
        if not self.destination_snapshot:
            errors.setdefault("destination_snapshot", []).append("Destination snapshot is required.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.provider_response:
            self.provider_response = {}
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportJob(TimeStampedModel):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="report_jobs",
    )
    data_center = models.ForeignKey(
        "datacenters.DataCenter",
        on_delete=models.CASCADE,
        related_name="report_jobs",
        blank=True,
        null=True,
    )
    definition = models.ForeignKey(
        ReportDefinition,
        on_delete=models.SET_NULL,
        related_name="jobs",
        blank=True,
        null=True,
    )
    template = models.ForeignKey(
        ReportTemplate,
        on_delete=models.SET_NULL,
        related_name="jobs",
        blank=True,
        null=True,
    )
    schedule = models.ForeignKey(
        "ReportSchedule",
        on_delete=models.SET_NULL,
        related_name="executions",
        blank=True,
        null=True,
    )
    trigger_source = models.CharField(max_length=30, choices=ReportJobTriggerSource.choices, default=ReportJobTriggerSource.MANUAL)
    scheduled_for = models.DateTimeField(blank=True, null=True, db_index=True)
    status = models.CharField(max_length=30, choices=ReportJobStatus.choices, default=ReportJobStatus.PENDING)
    progress_percent = models.PositiveSmallIntegerField(blank=True, null=True)
    current_stage = models.CharField(max_length=120, blank=True, null=True)
    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="report_jobs",
        blank=True,
        null=True,
    )
    queued_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    error_code = models.CharField(max_length=80, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    parent_job = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="retry_children",
        blank=True,
        null=True,
    )
    parameters = models.JSONField(default=dict, blank=True)
    definition_code_snapshot = models.CharField(max_length=100, blank=True, null=True)
    definition_version_snapshot = models.PositiveIntegerField(blank=True, null=True)
    template_name_snapshot = models.CharField(max_length=255, blank=True, null=True)
    template_version_snapshot = models.PositiveIntegerField(blank=True, null=True)
    template_config_snapshot = models.JSONField(default=dict, blank=True)
    parameters_snapshot = models.JSONField(default=dict, blank=True)
    scope_snapshot = models.JSONField(default=dict, blank=True)
    recipient_snapshot = models.JSONField(default=dict, blank=True)
    output_config_snapshot = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to="reports/", blank=True, null=True)

    class Meta:
        db_table = "report_jobs"
        indexes = [
            models.Index(fields=["organization", "data_center"]),
            models.Index(fields=["definition"]),
            models.Index(fields=["template"]),
            models.Index(fields=["schedule"]),
            models.Index(fields=["trigger_source"]),
            models.Index(fields=["status"]),
            models.Index(fields=["scheduled_for"]),
            models.Index(fields=["queued_at"]),
            models.Index(fields=["requested_by"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "scheduled_for"],
                condition=models.Q(schedule__isnull=False, scheduled_for__isnull=False),
                name="uq_report_job_schedule_scheduled_for",
            ),
        ]

    def __str__(self):
        return f"ReportJob {self.id}"

    @property
    def reference(self):
        return f"REPORT-{str(self.id).split('-')[0].upper()}" if self.id else None

    @property
    def report_type(self):
        if self.definition_id:
            return self.definition.code
        if self.template_id and self.template.report_type:
            return self.template.report_type
        if isinstance(self.parameters, dict):
            return canonical_report_definition_code(self.parameters.get("report_type")) or self.parameters.get("report_type")
        if isinstance(self.parameters_snapshot, dict):
            return canonical_report_definition_code(self.parameters_snapshot.get("report_type")) or self.parameters_snapshot.get("report_type")
        return None

    @property
    def legacy_status(self):
        raw_status = super().__getattribute__("status")
        legacy_map = {
            ReportJobStatus.QUEUED: ReportJobStatus.PENDING,
            ReportJobStatus.RUNNING: ReportJobStatus.PROCESSING,
            ReportJobStatus.SUCCEEDED: ReportJobStatus.COMPLETED,
            ReportJobStatus.PARTIALLY_SUCCEEDED: ReportJobStatus.COMPLETED,
        }
        return legacy_map.get(raw_status, raw_status)

    @property
    def primary_artifact(self):
        return self.artifacts.filter(
            artifact_type=ReportArtifactType.PRIMARY,
            status=ReportArtifactStatus.AVAILABLE,
        ).order_by("-created_at").first()

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.completed_at or timezone.now()
        return max(0, int((end - self.started_at).total_seconds()))

    @property
    def is_downloadable(self):
        if self.primary_artifact:
            return True
        raw_status = super().__getattribute__("status")
        return raw_status in {ReportJobStatus.SUCCEEDED, ReportJobStatus.PARTIALLY_SUCCEEDED, ReportJobStatus.COMPLETED} and bool(self.file)

    @property
    def can_retry(self):
        return super().__getattribute__("status") in {ReportJobStatus.FAILED}

    @property
    def can_cancel(self):
        return super().__getattribute__("status") in {
            ReportJobStatus.PENDING,
            ReportJobStatus.QUEUED,
            ReportJobStatus.RUNNING,
            ReportJobStatus.PROCESSING,
        }

    def mark_queued(self, *, save: bool = True):
        self.status = ReportJobStatus.QUEUED
        self.error_code = ""
        self.error_message = ""
        if save:
            self.save(update_fields=["status", "error_code", "error_message", "updated_at"])
        return self

    def mark_running(self, *, save: bool = True):
        self.status = ReportJobStatus.RUNNING
        self.started_at = self.started_at or timezone.now()
        self.error_code = ""
        self.error_message = ""
        if save:
            self.save(update_fields=["status", "started_at", "error_code", "error_message", "updated_at"])
        return self

    def update_progress(self, progress_percent: int | None = None, current_stage: str | None = None, *, save: bool = True):
        if progress_percent is not None:
            self.progress_percent = max(0, min(100, int(progress_percent)))
        if current_stage is not None:
            self.current_stage = current_stage
        if save:
            fields = ["updated_at"]
            if progress_percent is not None:
                fields.append("progress_percent")
            if current_stage is not None:
                fields.append("current_stage")
            self.save(update_fields=fields)
        return self

    def mark_succeeded(self, *, save: bool = True):
        self.status = ReportJobStatus.SUCCEEDED
        self.completed_at = self.completed_at or timezone.now()
        self.error_code = ""
        self.error_message = ""
        self.progress_percent = 100
        if save:
            self.save(update_fields=["status", "completed_at", "error_code", "error_message", "progress_percent", "updated_at"])
        return self

    def mark_partially_succeeded(self, *, save: bool = True):
        self.status = ReportJobStatus.PARTIALLY_SUCCEEDED
        self.completed_at = self.completed_at or timezone.now()
        self.error_code = ""
        self.error_message = ""
        if save:
            self.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])
        return self

    def mark_failed(self, *, error_code: str | None = None, error_message: str | None = None, save: bool = True):
        self.status = ReportJobStatus.FAILED
        self.completed_at = self.completed_at or timezone.now()
        self.error_code = error_code or self.error_code
        self.error_message = error_message or self.error_message or ""
        if save:
            self.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])
        return self

    def mark_cancelled(self, *, error_message: str | None = None, save: bool = True):
        self.status = ReportJobStatus.CANCELLED
        self.completed_at = self.completed_at or timezone.now()
        self.error_code = "JOB_CANCELLED"
        self.error_message = error_message or "Cancelled"
        if save:
            self.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])
        return self

    def _snapshot_scope(self):
        return {
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "data_center_id": str(self.data_center_id) if self.data_center_id else None,
            "schedule_id": str(self.schedule_id) if self.schedule_id else None,
            "template_id": str(self.template_id) if self.template_id else None,
            "definition_code": self.definition.code if self.definition_id else None,
        }

    def _snapshot_recipients(self):
        if self.schedule_id and hasattr(self.schedule, "recipient_entries"):
            entries = self.schedule.recipient_entries.filter(is_active=True)
            return [
                {
                    "channel": entry.channel,
                    "recipient_type": entry.recipient_type,
                    "destination": entry.destination,
                    "display_name": entry.display_name,
                    "user_id": str(entry.user_id) if entry.user_id else None,
                }
                for entry in entries
            ]
        return {
            "email_recipients": list(self.schedule.recipients) if self.schedule_id and isinstance(self.schedule.recipients, list) else list(self.parameters.get("recipients", [])) if isinstance(self.parameters, dict) else [],
            "sms_recipients": list(self.schedule.sms_recipients) if self.schedule_id and isinstance(self.schedule.sms_recipients, list) else list(self.parameters.get("sms_recipients", [])) if isinstance(self.parameters, dict) else [],
        }

    def _sync_snapshots(self):
        if self.definition_id and not self.definition_code_snapshot:
            self.definition_code_snapshot = self.definition.code
        if self.definition_id and not self.definition_version_snapshot:
            self.definition_version_snapshot = self.definition.version
        if self.template_id and not self.template_name_snapshot:
            self.template_name_snapshot = self.template.name
        if self.template_id and not self.template_version_snapshot:
            self.template_version_snapshot = self.template.version
        if self.template_id and not self.template_config_snapshot:
            self.template_config_snapshot = self.template.config if isinstance(self.template.config, dict) else {}
        if not self.parameters_snapshot:
            self.parameters_snapshot = self.parameters if isinstance(self.parameters, dict) else {}
        if not self.scope_snapshot:
            self.scope_snapshot = self._snapshot_scope()
        if not self.recipient_snapshot:
            self.recipient_snapshot = self._snapshot_recipients()
        if not self.output_config_snapshot:
            self.output_config_snapshot = {
                "primary_format": getattr(self.schedule, "primary_format", None) if self.schedule_id else None,
                "attachment_formats": list(getattr(self.schedule, "attachment_formats", [])) if self.schedule_id and isinstance(self.schedule.attachment_formats, list) else [],
            }

    def clean(self):
        super().clean()

        errors = {}

        if self.organization_id is None:
            errors.setdefault("organization", []).append("Organization is required.")
        if self.data_center_id and self.organization_id and self.data_center.organization_id != self.organization_id:
            errors.setdefault("data_center", []).append("Data center must belong to the selected organization.")
        if self.template_id and self.organization_id and self.template.organization_id not in {None, self.organization_id}:
            errors.setdefault("template", []).append("Template must belong to the selected organization or be global.")
        if self.definition_id and self.template_id and self.template.definition_id and self.template.definition_id != self.definition_id:
            errors.setdefault("definition", []).append("Definition must match the selected template definition.")
        if self.schedule_id and self.organization_id and self.schedule.organization_id != self.organization_id:
            errors.setdefault("schedule", []).append("Schedule must belong to the selected organization.")
        if self.parent_job_id and self.organization_id and self.parent_job.organization_id != self.organization_id:
            errors.setdefault("parent_job", []).append("Parent job must belong to the selected organization.")
        if not isinstance(self.parameters, dict):
            errors.setdefault("parameters", []).append("Parameters must be a dictionary/object.")
        if not isinstance(self.template_config_snapshot, dict):
            errors.setdefault("template_config_snapshot", []).append("Template snapshot must be a dictionary/object.")
        if not isinstance(self.parameters_snapshot, dict):
            errors.setdefault("parameters_snapshot", []).append("Parameters snapshot must be a dictionary/object.")
        if not isinstance(self.scope_snapshot, dict):
            errors.setdefault("scope_snapshot", []).append("Scope snapshot must be a dictionary/object.")
        if not isinstance(self.recipient_snapshot, (dict, list)):
            errors.setdefault("recipient_snapshot", []).append("Recipient snapshot must be a JSON object or array.")
        if not isinstance(self.output_config_snapshot, dict):
            errors.setdefault("output_config_snapshot", []).append("Output configuration snapshot must be a dictionary/object.")
        if self.progress_percent is not None and not (0 <= self.progress_percent <= 100):
            errors.setdefault("progress_percent", []).append("Progress percent must be between 0 and 100.")
        raw_status = super().__getattribute__("status")
        if raw_status not in ReportJobStatus.values:
            errors.setdefault("status", []).append("Unsupported job status.")
        if self.trigger_source not in ReportJobTriggerSource.values:
            errors.setdefault("trigger_source", []).append("Unsupported trigger source.")
        if self.scheduled_for and self.schedule_id and self.schedule_id and self.schedule.next_run_at and self.scheduled_for != self.schedule.next_run_at:
            # We do not force equality here because run-now jobs may reuse a schedule definition.
            pass

        if self._state.adding and self.requested_by_id is None and self.trigger_source not in {
            ReportJobTriggerSource.SYSTEM,
            ReportJobTriggerSource.SCHEDULED,
        }:
            errors.setdefault("requested_by", []).append("Requested by is required for user-created jobs.")

        if self.started_at and self.completed_at and self.started_at > self.completed_at:
            errors.setdefault("started_at", []).append("Started time cannot be after completed time.")
            errors.setdefault("completed_at", []).append("Completed time cannot be before started time.")

        if raw_status in {
            ReportJobStatus.RUNNING,
            ReportJobStatus.PROCESSING,
        } and self.started_at is None:
            errors.setdefault("started_at", []).append("Running jobs must have a started time.")

        if raw_status in {
            ReportJobStatus.SUCCEEDED,
            ReportJobStatus.PARTIALLY_SUCCEEDED,
            ReportJobStatus.FAILED,
            ReportJobStatus.CANCELLED,
            ReportJobStatus.COMPLETED,
        }:
            if self.started_at is None:
                errors.setdefault("started_at", []).append("Terminal jobs must have a started time.")
            if self.completed_at is None:
                errors.setdefault("completed_at", []).append("Terminal jobs must have a completed time.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.definition_id and isinstance(self.parameters, dict):
            self.parameters.setdefault("report_type", self.definition.code)
        if not self.definition_id and self.template_id and self.template.definition_id:
            self.definition = self.template.definition
        if self.schedule_id:
            if not self.definition_id and self.schedule.definition_id:
                self.definition = self.schedule.definition
            if not self.template_id and self.schedule.template_id:
                self.template = self.schedule.template
            if not self.scheduled_for:
                self.scheduled_for = self.schedule.next_run_at
        if self.definition_id and not self.definition_code_snapshot:
            self.definition_code_snapshot = self.definition.code
        if self.template_id and not self.template_name_snapshot:
            self.template_name_snapshot = self.template.name
        if self.definition_id and not self.definition_version_snapshot:
            self.definition_version_snapshot = self.definition.version
        if self.template_id and not self.template_version_snapshot:
            self.template_version_snapshot = self.template.version
        if self.template_id and not self.template_config_snapshot:
            self.template_config_snapshot = self.template.config if isinstance(self.template.config, dict) else {}
        if self.requested_by_id is None and self._state.adding and self.trigger_source in {
            ReportJobTriggerSource.SYSTEM,
            ReportJobTriggerSource.SCHEDULED,
        }:
            pass
        if not self.queued_at:
            self.queued_at = timezone.now()
        self._sync_snapshots()
        self.full_clean()
        return super().save(*args, **kwargs)
