from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time as time_cls

from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.common.permissions import user_has_permission
from apps.datacenters.models import DataCenter
from apps.organizations.models import Organization
from apps.notifications.models import NotificationChannel

from .enums import (
    ReportArtifactFormat,
    ReportDefinitionCategory,
    ReportDeliveryStatus,
    ReportJobStatusV2,
    ReportRecipientChannel,
    ReportScheduleStatus,
    ReportTriggerSource,
)
from .models import (
    ReportArtifact,
    ReportDefinition,
    ReportDelivery,
    ReportJob,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportScheduleRun,
    ReportTemplate,
)
from .services.configuration import build_report_template_options, validate_report_template_config
from .services.definitions import (
    build_definition_capabilities,
    get_active_definition_by_code,
    get_definition_by_code,
    validate_definition_request,
)
from .services.deliveries import build_report_delivery_summary, report_delivery_summary
from .services.factory import build_output_config_snapshot, build_parameters_snapshot, build_recipient_snapshot, build_scope_snapshot, build_source_event_snapshot, build_template_snapshot, create_report_job
from .services.permissions import (
    ensure_data_center_access,
    ensure_organization_access,
    ensure_schedule_scope,
    ensure_template_scope,
    report_artifact_allowed_actions,
    report_definition_allowed_actions,
    report_delivery_allowed_actions,
    report_job_allowed_actions,
    report_schedule_allowed_actions,
    report_schedule_run_allowed_actions,
    report_template_allowed_actions,
)
from .services.templates import create_report_template, update_report_template


def _model_id(value):
    if value is None:
        return None
    if hasattr(value, "pk"):
        return str(value.pk)
    return str(value)


def _user_summary(user):
    if not user:
        return None
    return {
        "id": str(user.pk),
        "username": getattr(user, "username", None),
        "email": getattr(user, "email", None),
        "full_name": getattr(user, "full_name", None) or getattr(user, "username", None) or getattr(user, "email", None),
    }


def _organization_summary(organization):
    if not organization:
        return None
    return {
        "id": str(organization.pk),
        "name": getattr(organization, "name", None),
        "code": getattr(organization, "code", None),
    }


def _data_center_summary(data_center):
    if not data_center:
        return None
    return {
        "id": str(data_center.pk),
        "name": getattr(data_center, "name", None),
        "code": getattr(data_center, "code", None),
    }


def _definition_summary(definition):
    if not definition:
        return None
    return {
        "code": definition.code,
        "name": definition.name,
        "category": definition.category,
        "description": definition.description,
        "is_active": definition.is_active,
        "version": definition.version,
        "supported_formats": list(definition.supported_formats or []),
        "supported_delivery_channels": list(definition.supported_delivery_channels or []),
        "requires_data_center": definition.requires_data_center,
        "requires_telemetry": definition.requires_telemetry,
    }


def _template_summary(template):
    if not template:
        return None
    return {
        "id": str(template.pk),
        "code": template.code,
        "name": template.name,
        "description": template.description,
        "version": template.version,
        "is_active": template.is_active,
        "primary_format": template.primary_format,
        "attachment_formats": list(template.attachment_formats or []),
        "definition": _definition_summary(template.definition),
    }


def _schedule_summary(schedule):
    if not schedule:
        return None
    return {
        "id": str(schedule.pk),
        "name": schedule.name,
        "status": schedule.status,
        "frequency": schedule.frequency,
        "frequency_label": getattr(schedule, "get_frequency_label", lambda: schedule.get_frequency_display())(),
        "timezone": schedule.timezone,
        "delivery_time": schedule.delivery_time.isoformat() if schedule.delivery_time else None,
        "days_of_week": list(schedule.days_of_week or []),
        "day_of_month": schedule.day_of_month,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "primary_format": schedule.primary_format or schedule.output_format,
        "attachment_formats": list(schedule.attachment_formats or []),
    }


def _artifact_summary(artifact):
    if not artifact:
        return None
    return {
        "id": str(artifact.pk),
        "format": artifact.format,
        "original_filename": artifact.original_filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "checksum_sha256": artifact.checksum_sha256,
        "retention_expires_at": artifact.retention_expires_at.isoformat() if artifact.retention_expires_at else None,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "download_url": f"/api/v1/reports/artifacts/{artifact.pk}/download/",
    }


def _delivery_recipient(delivery):
    recipient = getattr(delivery, "recipient", None) or ""
    if not recipient:
        return None
    if "@" in recipient:
        local, _, domain = recipient.partition("@")
        return f"{local[:2]}***@{domain}"
    if len(recipient) <= 4:
        return "***"
    return f"{recipient[:3]}***{recipient[-2:]}"


def _delivery_summary(delivery):
    if not delivery:
        return None
    return {
        "id": str(delivery.pk),
        "job": str(delivery.job_id) if delivery.job_id else None,
        "channel": delivery.channel,
        "recipient": _delivery_recipient(delivery),
        "status": delivery.status,
        "queued_at": delivery.queued_at.isoformat() if delivery.queued_at else None,
        "started_at": delivery.started_at.isoformat() if delivery.started_at else None,
        "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
        "failed_at": delivery.failed_at.isoformat() if delivery.failed_at else None,
        "retry_count": delivery.retry_count,
        "provider_message_id": delivery.provider_message_id or "",
        "error_code": delivery.error_code or "",
        "error_message": delivery.error_message or "",
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
        "allowed_actions": report_delivery_allowed_actions(None, delivery),
    }


def _schedule_recipient_summary(recipient):
    if not recipient:
        return None
    return {
        "id": str(recipient.pk),
        "channel": recipient.channel,
        "display_name": recipient.display_name,
        "email_address": recipient.email_address,
        "phone_number": recipient.phone_number,
        "is_active": recipient.is_active,
    }


def _normalize_string_list(values):
    result = []
    seen = set()
    for value in values or []:
        candidate = str(value).strip().upper()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _parse_time_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, time_cls):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise serializers.ValidationError({"delivery_time": "Invalid delivery time."})


def _normalize_contact_list(values):
    result = []
    seen = set()
    for value in values or []:
        candidate = str(value).strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


class ReportDefinitionSerializer(serializers.ModelSerializer):
    allowed_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportDefinition
        fields = (
            "code",
            "name",
            "description",
            "category",
            "parameter_schema",
            "supported_formats",
            "supported_delivery_channels",
            "requires_data_center",
            "requires_telemetry",
            "is_active",
            "version",
            "allowed_actions",
        )
        read_only_fields = fields

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_definition_allowed_actions(getattr(request, "user", None), obj)


class ReportDefinitionSchemaSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True)
    parameter_schema = serializers.JSONField(read_only=True)
    supported_formats = serializers.ListField(child=serializers.CharField(), read_only=True)
    supported_delivery_channels = serializers.ListField(child=serializers.CharField(), read_only=True)
    requires_data_center = serializers.BooleanField(read_only=True)
    requires_telemetry = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    version = serializers.IntegerField(read_only=True)


class ReportDashboardQuerySerializer(serializers.Serializer):
    organization = serializers.UUIDField(required=False)
    data_center = serializers.UUIDField(required=False)
    start_at = serializers.DateTimeField(required=False)
    end_at = serializers.DateTimeField(required=False)
    timezone = serializers.CharField(required=False, allow_blank=True)


class ReportDashboardResponseSerializer(serializers.Serializer):
    range = serializers.JSONField(read_only=True)
    summary = serializers.JSONField(read_only=True)
    generation_trend = serializers.JSONField(read_only=True)
    generation_trend_current_month = serializers.JSONField(read_only=True)
    by_definition = serializers.JSONField(read_only=True)
    by_format = serializers.JSONField(read_only=True)
    delivery_summary = serializers.JSONField(read_only=True)
    recent_jobs = serializers.JSONField(read_only=True)
    upcoming_schedules = serializers.JSONField(read_only=True)
    recent_failures = serializers.JSONField(read_only=True)
    frequent_templates = serializers.JSONField(read_only=True)
    schedule_health = serializers.JSONField(read_only=True)
    operational_health = serializers.JSONField(read_only=True)


class ReportTemplateReadSerializer(serializers.ModelSerializer):
    organization = serializers.SerializerMethodField(read_only=True)
    data_center = serializers.SerializerMethodField(read_only=True)
    definition = serializers.SerializerMethodField(read_only=True)
    configuration = serializers.SerializerMethodField(read_only=True)
    created_by = serializers.SerializerMethodField(read_only=True)
    updated_by = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportTemplate
        fields = (
            "id",
            "code",
            "name",
            "description",
            "organization",
            "data_center",
            "definition",
            "configuration",
            "default_parameters",
            "primary_format",
            "attachment_formats",
            "include_charts",
            "include_raw_data",
            "version",
            "is_active",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "allowed_actions",
        )
        read_only_fields = fields

    def get_organization(self, obj):
        return _organization_summary(obj.organization)

    def get_data_center(self, obj):
        return _data_center_summary(getattr(obj, "data_center", None))

    def get_definition(self, obj):
        return _definition_summary(obj.definition)

    def get_configuration(self, obj):
        return deepcopy(obj.config if isinstance(obj.config, dict) else {})

    def get_created_by(self, obj):
        return None

    def get_updated_by(self, obj):
        return _user_summary(getattr(obj, "updated_by", None))

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_template_allowed_actions(getattr(request, "user", None), obj)


class ReportTemplateWriteSerializer(serializers.Serializer):
    organization = serializers.CharField()
    data_center = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    definition = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    name = serializers.CharField()
    code = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    configuration = serializers.JSONField(required=False, default=dict)
    default_parameters = serializers.JSONField(required=False, default=dict)
    primary_format = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attachment_formats = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    include_charts = serializers.BooleanField(required=False, default=False)
    include_raw_data = serializers.BooleanField(required=False, default=False)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization = attrs.get("organization")
        data_center = attrs.get("data_center")
        definition_code = attrs.get("definition")
        name = attrs.get("name")
        code = attrs.get("code")
        description = attrs.get("description")
        configuration = attrs.get("configuration") or {}
        if self.instance is not None:
            if "organization" not in self.initial_data:
                organization = self.instance.organization
            if "data_center" not in self.initial_data:
                data_center = getattr(self.instance, "data_center", None)
            if "definition" not in self.initial_data:
                definition_code = getattr(self.instance.definition, "code", None)
            if "name" not in self.initial_data:
                name = self.instance.name
            if "code" not in self.initial_data:
                code = self.instance.code
            if "description" not in self.initial_data:
                description = self.instance.description
            if "configuration" not in self.initial_data:
                configuration = deepcopy(self.instance.config if isinstance(self.instance.config, dict) else {})
            if "default_parameters" not in self.initial_data:
                attrs["default_parameters"] = deepcopy(self.instance.default_parameters if isinstance(self.instance.default_parameters, dict) else {})
            if "primary_format" not in self.initial_data:
                attrs["primary_format"] = self.instance.primary_format
            if "attachment_formats" not in self.initial_data:
                attrs["attachment_formats"] = deepcopy(self.instance.attachment_formats if isinstance(self.instance.attachment_formats, list) else [])
            if "include_charts" not in self.initial_data:
                attrs["include_charts"] = self.instance.include_charts
            if "include_raw_data" not in self.initial_data:
                attrs["include_raw_data"] = self.instance.include_raw_data
        if not isinstance(configuration, dict):
            raise serializers.ValidationError({"configuration": "Configuration must be a dictionary/object."})
        if data_center in ("", None):
            data_center = None

        if organization is None:
            raise serializers.ValidationError({"organization": "Selected organization does not exist."})
        if not isinstance(organization, Organization):
            organization = Organization.objects.filter(pk=organization).first()
        if organization is None:
            raise serializers.ValidationError({"organization": "Selected organization does not exist."})
        ensure_organization_access(user, organization)

        if data_center not in (None, ""):
            if not isinstance(data_center, DataCenter):
                data_center = DataCenter.objects.filter(pk=data_center).first()
            if data_center is None:
                raise serializers.ValidationError({"data_center": "Selected data center does not exist."})
            ensure_data_center_access(user, data_center)
            if data_center.organization_id != organization.id:
                raise serializers.ValidationError({"data_center": "Data center must belong to the selected organization."})

        if definition_code not in (None, ""):
            if isinstance(definition_code, ReportDefinition):
                definition = definition_code
            else:
                definition = get_definition_by_code(definition_code)
        else:
            definition = None

        if definition is None:
            raise serializers.ValidationError({"definition": "A matching active report definition is required."})

        try:
            validated = validate_definition_request(
                definition,
                organization,
                data_center,
                attrs.get("default_parameters") or configuration.get("default_parameters") or {},
                attrs.get("primary_format") or configuration.get("output_format"),
                attrs.get("attachment_formats") or configuration.get("attachment_formats") or [],
                configuration.get("delivery_channels") or [],
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"configuration": exc.messages})

        attrs["organization"] = organization
        attrs["data_center"] = data_center
        attrs["definition"] = definition
        attrs["name"] = name
        attrs["code"] = code
        attrs["description"] = description
        attrs["configuration"] = validate_report_template_config(
            configuration,
            existing_config={},
            definition_code=definition.code if definition else None,
        )
        attrs["default_parameters"] = validated["parameters"]
        attrs["primary_format"] = validated["primary_format"]
        attrs["attachment_formats"] = validated["attachment_formats"]
        attrs["include_charts"] = bool(attrs.get("include_charts", False))
        attrs["include_raw_data"] = bool(attrs.get("include_raw_data", False))
        return attrs

    def _save(self, instance, validated_data):
        actor = getattr(self.context.get("request"), "user", None)
        organization = validated_data["organization"]
        data_center = validated_data.get("data_center")
        definition = validated_data["definition"]
        configuration = validated_data.get("configuration") or {}
        default_parameters = validated_data.get("default_parameters") or {}
        primary_format = validated_data.get("primary_format")
        attachment_formats = validated_data.get("attachment_formats") or []
        include_charts = validated_data.get("include_charts", False)
        include_raw_data = validated_data.get("include_raw_data", False)
        name = validated_data.get("name")
        code = validated_data.get("code")
        description = validated_data.get("description")

        if instance is None:
            return create_report_template(
                actor=actor,
                organization=organization,
                data_center=data_center,
                definition_code=definition.code,
                config=configuration,
                name=name,
                code=code,
                description=description,
                default_parameters=default_parameters,
                primary_format=primary_format,
                attachment_formats=attachment_formats,
                include_charts=include_charts,
                include_raw_data=include_raw_data,
                updated_by=actor,
            )
        return update_report_template(
            instance,
            actor=actor,
            organization=organization,
            data_center=data_center,
            definition_code=definition.code,
            config=configuration,
            name=name,
            description=description,
            default_parameters=default_parameters,
            primary_format=primary_format,
            attachment_formats=attachment_formats,
            include_charts=include_charts,
            include_raw_data=include_raw_data,
            updated_by=actor,
        )

    def create(self, validated_data):
        return self._save(None, validated_data)

    def update(self, instance, validated_data):
        return self._save(instance, validated_data)


class ReportScheduleRecipientSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=ReportRecipientChannel.choices)
    recipient_type = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    destination = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    display_name = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    email_address = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    phone_number = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        channel = attrs.get("channel")
        email_address = (attrs.get("email_address") or "").strip()
        phone_number = (attrs.get("phone_number") or "").strip()
        if channel == ReportRecipientChannel.EMAIL:
            if not email_address:
                raise serializers.ValidationError({"email_address": "Email recipients require an email address."})
            attrs["email_address"] = email_address.lower()
            attrs["phone_number"] = None
            attrs["recipient_type"] = ReportRecipientChannel.EMAIL
            attrs["destination"] = attrs["email_address"]
        elif channel == ReportRecipientChannel.SMS:
            if not phone_number:
                raise serializers.ValidationError({"phone_number": "SMS recipients require a phone number."})
            attrs["phone_number"] = phone_number
            attrs["email_address"] = None
            attrs["recipient_type"] = ReportRecipientChannel.SMS
            attrs["destination"] = attrs["phone_number"]
        attrs["display_name"] = (attrs.get("display_name") or "").strip()
        attrs["is_active"] = bool(attrs.get("is_active", True))
        return attrs


class ReportScheduleReadSerializer(serializers.ModelSerializer):
    organization = serializers.SerializerMethodField(read_only=True)
    data_center = serializers.SerializerMethodField(read_only=True)
    template = serializers.SerializerMethodField(read_only=True)
    definition = serializers.SerializerMethodField(read_only=True)
    frequency_label = serializers.SerializerMethodField(read_only=True)
    recent_runs = serializers.SerializerMethodField(read_only=True)
    recipients = serializers.SerializerMethodField(read_only=True)
    sms_recipients = serializers.SerializerMethodField(read_only=True)
    send_sms = serializers.SerializerMethodField(read_only=True)
    created_by = serializers.SerializerMethodField(read_only=True)
    updated_by = serializers.SerializerMethodField(read_only=True)
    last_result = serializers.SerializerMethodField(read_only=True)
    last_failure_at = serializers.SerializerMethodField(read_only=True)
    delivery_summary = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportSchedule
        fields = (
            "id",
            "name",
            "organization",
            "data_center",
            "template",
            "definition",
            "status",
            "frequency",
            "frequency_label",
            "timezone",
            "delivery_time",
            "days_of_week",
            "day_of_month",
            "start_at",
            "end_at",
            "next_run_at",
            "last_run_at",
            "last_success_at",
            "last_failure_at",
            "parameter_overrides",
            "recent_runs",
            "recipients",
            "sms_recipients",
            "send_sms",
            "primary_format",
            "attachment_formats",
            "last_result",
            "last_failure_at",
            "delivery_summary",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "allowed_actions",
        )
        read_only_fields = fields

    def get_organization(self, obj):
        return _organization_summary(obj.organization)

    def get_data_center(self, obj):
        return _data_center_summary(getattr(obj, "data_center", None))

    def get_template(self, obj):
        return _template_summary(obj.template)

    def get_definition(self, obj):
        return _definition_summary(obj.template.definition if obj.template_id and obj.template and obj.template.definition_id else None)

    def get_frequency_label(self, obj):
        return getattr(obj, "get_frequency_label", lambda: obj.get_frequency_display())()

    def get_recent_runs(self, obj):
        runs = getattr(obj, "_prefetched_recent_runs", None)
        if runs is None:
            runs = (
                obj.runs.select_related("schedule", "job", "requested_by")
                .prefetch_related("deliveries", "job__artifacts")
                .order_by("-created_at")[:3]
            )
        else:
            runs = list(runs)[:3]
        return [ReportScheduleRunSerializer(run, context=self.context).data for run in runs]

    def get_recipients(self, obj):
        structured = getattr(obj, "structured_recipients", None)
        rows = structured.all() if structured is not None else []
        return [_schedule_recipient_summary(row) for row in rows if row.channel == ReportRecipientChannel.EMAIL]

    def get_sms_recipients(self, obj):
        structured = getattr(obj, "structured_recipients", None)
        rows = structured.all() if structured is not None else []
        return [_schedule_recipient_summary(row) for row in rows if row.channel == ReportRecipientChannel.SMS]

    def get_send_sms(self, obj):
        return bool(getattr(obj, "send_sms", False))

    def get_created_by(self, obj):
        return _user_summary(getattr(obj, "created_by", None))

    def get_updated_by(self, obj):
        return _user_summary(getattr(obj, "updated_by", None))

    def get_last_result(self, obj):
        if obj.last_job_id:
            return obj.last_job.status
        return obj.last_delivery_status

    def get_last_failure_at(self, obj):
        if obj.last_job_id and getattr(obj.last_job, "status", None) == ReportJobStatusV2.FAILED:
            failed_at = getattr(obj.last_job, "failed_at", None) or getattr(obj.last_job, "completed_at", None)
            return failed_at.isoformat() if failed_at else None
        return None

    def get_delivery_summary(self, obj):
        if obj.last_job_id:
            return build_report_delivery_summary(obj.last_job)
        status = obj.last_delivery_status or "PENDING"
        label = "No runs yet" if not obj.last_run_at and not obj.last_job_id else status
        return {
            "status": status,
            "label": label,
            "totals": {
                "total": 0,
                "sent": 0,
                "failed": 0,
                "pending": 0,
                "queued": 0,
                "delivering": 0,
                "cancelled": 0,
            },
            "channels": {},
        }

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_schedule_allowed_actions(getattr(request, "user", None), obj)


class ReportScheduleWriteSerializer(serializers.Serializer):
    organization = serializers.CharField()
    data_center = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    template = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=ReportScheduleStatus.choices, required=False, default=ReportScheduleStatus.ACTIVE)
    frequency = serializers.CharField()
    timezone = serializers.CharField(required=False, default="Asia/Dhaka")
    delivery_time = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    days_of_week = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    day_of_month = serializers.IntegerField(required=False, allow_null=True)
    start_at = serializers.DateTimeField(required=False, allow_null=True)
    end_at = serializers.DateTimeField(required=False, allow_null=True)
    parameter_overrides = serializers.JSONField(required=False, default=dict)
    recipients = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    sms_recipients = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    send_sms = serializers.BooleanField(required=False, default=False)
    primary_format = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    attachment_formats = serializers.ListField(child=serializers.CharField(), required=False, default=list)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization = attrs.get("organization")
        data_center = attrs.get("data_center")
        template = attrs.get("template")
        name = attrs.get("name")
        frequency = attrs.get("frequency")
        days_of_week = attrs.get("days_of_week")
        day_of_month = attrs.get("day_of_month")
        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")
        parameter_overrides = attrs.get("parameter_overrides")
        recipients = attrs.get("recipients")
        sms_recipients = attrs.get("sms_recipients")
        send_sms = attrs.get("send_sms")
        primary_format = attrs.get("primary_format")
        attachment_formats = attrs.get("attachment_formats")

        if self.instance is not None:
            if "organization" not in self.initial_data:
                organization = self.instance.organization
            if "data_center" not in self.initial_data:
                data_center = getattr(self.instance, "data_center", None)
            if "template" not in self.initial_data:
                template = self.instance.template
            if "name" not in self.initial_data:
                name = self.instance.name
            if "frequency" not in self.initial_data:
                frequency = self.instance.frequency
            if "status" not in self.initial_data:
                attrs["status"] = self.instance.status
            if "timezone" not in self.initial_data:
                attrs["timezone"] = self.instance.timezone
            if "delivery_time" not in self.initial_data:
                attrs["delivery_time"] = self.instance.delivery_time
            if "days_of_week" not in self.initial_data:
                days_of_week = deepcopy(self.instance.days_of_week if isinstance(self.instance.days_of_week, list) else [])
            if "day_of_month" not in self.initial_data:
                day_of_month = self.instance.day_of_month
            if "start_at" not in self.initial_data:
                start_at = self.instance.start_at
            if "end_at" not in self.initial_data:
                end_at = self.instance.end_at
            if "parameter_overrides" not in self.initial_data:
                parameter_overrides = deepcopy(self.instance.parameter_overrides if isinstance(self.instance.parameter_overrides, dict) else {})
            if "recipients" not in self.initial_data:
                structured = list(self.instance.structured_recipients.all())
                recipients = [
                {
                    "channel": row.channel,
                    "recipient_type": row.recipient_type,
                    "destination": row.destination,
                    "display_name": row.display_name,
                    "email_address": row.email_address,
                    "phone_number": row.phone_number,
                    "is_active": row.is_active,
                    }
                    for row in structured
                    if row.channel == ReportRecipientChannel.EMAIL
                ]
                attrs["recipients"] = recipients
            if "sms_recipients" not in self.initial_data:
                structured = list(self.instance.structured_recipients.all())
                sms_recipients = [
                {
                    "channel": row.channel,
                    "recipient_type": row.recipient_type,
                    "destination": row.destination,
                    "display_name": row.display_name,
                    "email_address": row.email_address,
                    "phone_number": row.phone_number,
                    "is_active": row.is_active,
                    }
                    for row in structured
                    if row.channel == ReportRecipientChannel.SMS
                ]
                attrs["sms_recipients"] = sms_recipients
            if "send_sms" not in self.initial_data:
                send_sms = self.instance.send_sms
            if "primary_format" not in self.initial_data:
                primary_format = self.instance.primary_format
            if "attachment_formats" not in self.initial_data:
                attachment_formats = deepcopy(self.instance.attachment_formats if isinstance(self.instance.attachment_formats, list) else [])

        if data_center in ("", None):
            data_center = None
        if template in ("", None):
            template = None

        if organization is not None and not isinstance(organization, Organization):
            organization = Organization.objects.filter(pk=organization).first()
        if organization is None:
            raise serializers.ValidationError({"organization": "Selected organization does not exist."})
        ensure_organization_access(user, organization)

        if data_center not in (None, ""):
            if not isinstance(data_center, DataCenter):
                data_center = DataCenter.objects.filter(pk=data_center).first()
            if data_center is None:
                raise serializers.ValidationError({"data_center": "Selected data center does not exist."})
            ensure_data_center_access(user, data_center)
            if data_center.organization_id != organization.id:
                raise serializers.ValidationError({"data_center": "Data center must belong to the selected organization."})

        if template is not None and not isinstance(template, ReportTemplate):
            template = ReportTemplate.objects.filter(pk=template).select_related("definition", "organization").first()
            if template is None:
                raise serializers.ValidationError({"template": "Selected template does not exist."})
            ensure_template_scope(template, organization, data_center)

        if template is None:
            raise serializers.ValidationError({"template": "A report template is required."})
        if not template.definition_id or not template.definition.is_active:
            raise serializers.ValidationError({"definition": "Selected report definition is inactive or unavailable."})

        raw_recipients = attrs.get("recipients") or []
        raw_sms_recipients = attrs.get("sms_recipients") or []
        normalized_recipients = []
        normalized_sms_recipients = []
        email_seen = set()
        sms_seen = set()

        email_field = serializers.EmailField()

        for item in raw_recipients:
            if isinstance(item, str):
                email_address = email_field.run_validation(item.strip())
                key = str(email_address).lower()
                if key in email_seen:
                    continue
                email_seen.add(key)
                normalized_recipients.append(
                    {
                        "channel": ReportRecipientChannel.EMAIL,
                        "display_name": "",
                        "email_address": key,
                        "phone_number": None,
                        "is_active": True,
                    }
                )
                continue
            if not isinstance(item, dict):
                raise serializers.ValidationError({"recipients": "Recipient emails must be strings or objects."})
            recipient_serializer = ReportScheduleRecipientSerializer(data=item)
            recipient_serializer.is_valid(raise_exception=True)
            cleaned = recipient_serializer.validated_data
            if cleaned["channel"] != ReportRecipientChannel.EMAIL:
                raise serializers.ValidationError({"recipients": "Recipient Email entries must be email addresses."})
            key = cleaned.get("email_address")
            if key in email_seen:
                continue
            email_seen.add(key)
            normalized_recipients.append(cleaned)

        for item in raw_sms_recipients:
            if isinstance(item, str):
                phone_number = str(item).strip()
                if not phone_number:
                    continue
                if phone_number in sms_seen:
                    continue
                sms_seen.add(phone_number)
                normalized_sms_recipients.append(
                    {
                        "channel": ReportRecipientChannel.SMS,
                        "display_name": "",
                        "email_address": None,
                        "phone_number": phone_number,
                        "is_active": True,
                    }
                )
                continue
            if not isinstance(item, dict):
                raise serializers.ValidationError({"sms_recipients": "SMS recipients must be strings or objects."})
            recipient_serializer = ReportScheduleRecipientSerializer(data=item)
            recipient_serializer.is_valid(raise_exception=True)
            cleaned = recipient_serializer.validated_data
            if cleaned["channel"] != ReportRecipientChannel.SMS:
                raise serializers.ValidationError({"sms_recipients": "SMS recipient entries must use the SMS channel."})
            key = cleaned.get("phone_number")
            if key in sms_seen:
                continue
            sms_seen.add(key)
            normalized_sms_recipients.append(cleaned)

        attrs["organization"] = organization
        attrs["data_center"] = data_center
        attrs["template"] = template
        attrs["name"] = name
        attrs["status"] = str(attrs.get("status") or ReportScheduleStatus.ACTIVE).upper()
        attrs["timezone"] = str(attrs.get("timezone") or "Asia/Dhaka").strip() or "Asia/Dhaka"
        attrs["frequency"] = frequency
        attrs["parameter_overrides"] = parameter_overrides or {}
        attrs["recipients"] = normalized_recipients if recipients is not None else recipients
        attrs["sms_recipients"] = normalized_sms_recipients if sms_recipients is not None else sms_recipients
        attrs["send_sms"] = bool(send_sms)
        attrs["primary_format"] = (primary_format or template.primary_format or template.config.get("output_format") or "CSV").upper()
        attrs["attachment_formats"] = _normalize_string_list(attachment_formats or template.attachment_formats or [])
        attrs["days_of_week"] = days_of_week or []
        attrs["day_of_month"] = day_of_month
        attrs["start_at"] = start_at
        attrs["end_at"] = end_at

        try:
            validate_definition_request(
                template.definition,
                organization,
                data_center,
                attrs["parameter_overrides"],
                attrs["primary_format"],
                attrs["attachment_formats"],
                [recipient["channel"] for recipient in normalized_recipients + (normalized_sms_recipients if attrs["send_sms"] else [])],
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"template": exc.messages})

        return attrs

    def _sync_recipients(self, schedule, recipients, sms_recipients):
        existing = list(schedule.structured_recipients.all())
        if existing:
            schedule.structured_recipients.all().delete()
        rows = []
        for recipient in recipients:
            rows.append(
                ReportScheduleRecipient(
                    schedule=schedule,
                    channel=recipient["channel"],
                    recipient_type=recipient.get("recipient_type") or recipient["channel"],
                    destination=recipient.get("destination") or recipient.get("email_address") or recipient.get("phone_number") or "",
                    display_name=recipient.get("display_name") or "",
                    email_address=recipient.get("email_address"),
                    phone_number=recipient.get("phone_number"),
                    is_active=recipient.get("is_active", True),
                )
            )
        for recipient in sms_recipients:
            rows.append(
                ReportScheduleRecipient(
                    schedule=schedule,
                    channel=recipient["channel"],
                    recipient_type=recipient.get("recipient_type") or recipient["channel"],
                    destination=recipient.get("destination") or recipient.get("email_address") or recipient.get("phone_number") or "",
                    display_name=recipient.get("display_name") or "",
                    email_address=recipient.get("email_address"),
                    phone_number=recipient.get("phone_number"),
                    is_active=recipient.get("is_active", True),
                )
            )
        if rows:
            ReportScheduleRecipient.objects.bulk_create(rows)

    def _save(self, instance, validated_data):
        actor = getattr(self.context.get("request"), "user", None)
        organization = validated_data["organization"]
        data_center = validated_data.get("data_center")
        template = validated_data["template"]
        recipients = validated_data.get("recipients") or []
        sms_recipients = validated_data.get("sms_recipients") or []
        status_value = validated_data.get("status", ReportScheduleStatus.ACTIVE)
        defaults = {
            "organization": organization,
            "data_center": data_center,
            "template": template,
            "name": validated_data["name"],
            "frequency": validated_data["frequency"],
            "timezone": validated_data["timezone"],
            "delivery_time": _parse_time_value(validated_data.get("delivery_time") or template.config.get("delivery_time")) or time_cls(6, 0),
            "days_of_week": validated_data.get("days_of_week") or [],
            "day_of_month": validated_data.get("day_of_month"),
            "start_at": validated_data.get("start_at"),
            "end_at": validated_data.get("end_at"),
            "parameter_overrides": validated_data.get("parameter_overrides") or {},
            "primary_format": validated_data.get("primary_format"),
            "attachment_formats": validated_data.get("attachment_formats") or [],
            "send_sms": bool(validated_data.get("send_sms", False)),
            "status": status_value,
            "created_by": actor if instance is None else getattr(instance, "created_by", None),
            "updated_by": actor,
        }

        if instance is None:
            schedule = ReportSchedule(**defaults)
            schedule.full_clean()
            schedule.save()
        else:
            for field_name, value in defaults.items():
                if field_name in {"created_by"}:
                    continue
                setattr(instance, field_name, value)
            instance.full_clean()
            instance.save()
            schedule = instance

        self._sync_recipients(schedule, recipients, sms_recipients)
        return schedule

    def create(self, validated_data):
        return self._save(None, validated_data)

    def update(self, instance, validated_data):
        return self._save(instance, validated_data)


class ReportScheduleRunSerializer(serializers.ModelSerializer):
    schedule = serializers.SerializerMethodField(read_only=True)
    job = serializers.SerializerMethodField(read_only=True)
    artifact_formats = serializers.SerializerMethodField(read_only=True)
    delivery_summary = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)
    duration_seconds = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportScheduleRun
        fields = (
            "id",
            "schedule",
            "job",
            "trigger_source",
            "scheduled_for",
            "window_start",
            "window_end",
            "status",
            "queued_at",
            "started_at",
            "completed_at",
            "duration_seconds",
            "error_message",
            "artifact_formats",
            "delivery_summary",
            "allowed_actions",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_schedule(self, obj):
        return _schedule_summary(obj.schedule)

    def get_job(self, obj):
        return _job_summary(obj.job)

    def get_artifact_formats(self, obj):
        job = obj.job
        if not job:
            return []
        artifacts = getattr(job, "_prefetched_artifacts", None)
        if artifacts is None:
            artifacts = list(job.artifacts.all())
        return [artifact.format for artifact in artifacts]

    def get_delivery_summary(self, obj):
        job = obj.job
        if not job:
            return {}
        return build_report_delivery_summary(job)

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_schedule_run_allowed_actions(getattr(request, "user", None), obj)

    def get_duration_seconds(self, obj):
        if not obj.started_at:
            return None
        end = obj.completed_at or timezone.now()
        return max(0, int((end - obj.started_at).total_seconds()))


class ReportArtifactSerializer(serializers.ModelSerializer):
    job = serializers.SerializerMethodField(read_only=True)
    download_url = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportArtifact
        fields = (
            "id",
            "job",
            "format",
            "original_filename",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "retention_expires_at",
            "download_url",
            "allowed_actions",
            "created_at",
        )
        read_only_fields = fields

    def get_job(self, obj):
        return _job_summary(obj.job)

    def get_download_url(self, obj):
        return f"/api/v1/reports/artifacts/{obj.pk}/download/"

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_artifact_allowed_actions(getattr(request, "user", None), obj)


class ReportDeliverySerializer(serializers.ModelSerializer):
    job = serializers.SerializerMethodField(read_only=True)
    recipient = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportDelivery
        fields = (
            "id",
            "job",
            "channel",
            "recipient",
            "status",
            "queued_at",
            "started_at",
            "sent_at",
            "failed_at",
            "retry_count",
            "provider_message_id",
            "error_code",
            "error_message",
            "allowed_actions",
            "created_at",
        )
        read_only_fields = fields

    def get_job(self, obj):
        return _job_summary(obj.job)

    def get_recipient(self, obj):
        return _delivery_recipient(obj)

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_delivery_allowed_actions(getattr(request, "user", None), obj)


class ReportJobListSerializer(serializers.ModelSerializer):
    organization = serializers.SerializerMethodField(read_only=True)
    data_center = serializers.SerializerMethodField(read_only=True)
    definition = serializers.SerializerMethodField(read_only=True)
    template = serializers.SerializerMethodField(read_only=True)
    schedule = serializers.SerializerMethodField(read_only=True)
    requested_by = serializers.SerializerMethodField(read_only=True)
    artifacts = serializers.SerializerMethodField(read_only=True)
    delivery_summary = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)
    duration_seconds = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportJob
        fields = (
            "id",
            "definition",
            "template",
            "schedule",
            "trigger_source",
            "requested_by",
            "organization",
            "data_center",
            "status",
            "progress_percent",
            "progress_message",
            "queued_at",
            "started_at",
            "completed_at",
            "failed_at",
            "cancelled_at",
            "duration_seconds",
            "artifacts",
            "delivery_summary",
            "error_code",
            "error_message",
            "created_at",
            "allowed_actions",
        )
        read_only_fields = fields

    def get_definition(self, obj):
        return _definition_summary(obj.definition)

    def get_organization(self, obj):
        return _organization_summary(obj.organization)

    def get_data_center(self, obj):
        return _data_center_summary(getattr(obj, "data_center", None))

    def get_template(self, obj):
        return _template_summary(obj.template)

    def get_schedule(self, obj):
        return _schedule_summary(obj.schedule)

    def get_requested_by(self, obj):
        return _user_summary(obj.requested_by)

    def get_artifacts(self, obj):
        artifacts = getattr(obj, "_prefetched_artifacts", None)
        if artifacts is None:
            artifacts = list(obj.artifacts.all())
        return [_artifact_summary(artifact) for artifact in artifacts]

    def get_delivery_summary(self, obj):
        return report_delivery_summary(obj)

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        return report_job_allowed_actions(getattr(request, "user", None), obj)

    def get_duration_seconds(self, obj):
        if not obj.started_at:
            return None
        end = obj.completed_at or timezone.now()
        return max(0, int((end - obj.started_at).total_seconds()))


class ReportJobDetailSerializer(ReportJobListSerializer):
    parameters_snapshot = serializers.JSONField(read_only=True)
    template_snapshot = serializers.JSONField(read_only=True)
    scope_snapshot = serializers.JSONField(read_only=True)
    output_config_snapshot = serializers.JSONField(read_only=True)
    recipient_snapshot = serializers.JSONField(read_only=True)
    source_event_snapshot = serializers.JSONField(read_only=True)

    class Meta(ReportJobListSerializer.Meta):
        fields = ReportJobListSerializer.Meta.fields + (
            "parameters_snapshot",
            "template_snapshot",
            "scope_snapshot",
            "output_config_snapshot",
            "recipient_snapshot",
            "source_event_snapshot",
        )
        read_only_fields = fields


def _job_summary(job):
    if not job:
        return None
    return {
        "id": str(job.pk),
        "definition": _definition_summary(job.definition),
        "template": _template_summary(job.template),
        "schedule": _schedule_summary(job.schedule),
        "trigger_source": job.trigger_source,
        "requested_by": _user_summary(job.requested_by),
        "organization": _organization_summary(job.organization),
        "data_center": _data_center_summary(getattr(job, "data_center", None)),
        "status": job.status,
        "progress_percent": job.progress_percent,
        "progress_message": job.progress_message,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "failed_at": job.failed_at.isoformat() if job.failed_at else None,
        "cancelled_at": job.cancelled_at.isoformat() if job.cancelled_at else None,
        "duration_seconds": job.duration_seconds,
        "artifacts": [_artifact_summary(artifact) for artifact in getattr(job, "_prefetched_artifacts", None) or job.artifacts.all()],
        "delivery_summary": report_delivery_summary(job),
        "error_code": job.error_code or "",
        "error_message": job.error_message or "",
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "allowed_actions": report_job_allowed_actions(None, job),
    }
