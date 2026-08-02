from __future__ import annotations

from datetime import datetime, time as time_cls

from rest_framework import serializers

from apps.common.access import get_access_scope
from apps.organizations.models import Organization

from .constants import (
    REPORT_TYPE_LABELS,
    normalize_report_format,
    normalize_report_frequency,
    normalize_report_type,
)
from .models import (
    ReportArtifact,
    ReportDelivery,
    ReportDefinition,
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleStatus,
    ReportScheduleRecipient,
    ReportTemplate,
)


def _user_can_access_organization(user, organization_id):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    scope = get_access_scope(user)
    if scope["global_access"]:
        return True
    return organization_id in scope["organization_ids"]


def _user_can_access_data_center(user, data_center_id):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    scope = get_access_scope(user)
    if scope["global_access"]:
        return True
    return data_center_id in scope["data_center_ids"]


def _normalize_delivery_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, time_cls):
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


def _user_phone(user):
    if not user:
        return None
    for field in ("phone", "mobile", "phone_number", "msisdn"):
        value = getattr(user, field, None)
        if value:
            phone = str(value).strip()
            if phone:
                return phone
    return None


def _primary_artifact_url(job, request=None):
    artifact = getattr(job, "primary_artifact", None)
    if artifact and getattr(artifact, "file", None):
        try:
            if request:
                return request.build_absolute_uri(artifact.file.url)
            return artifact.file.url
        except Exception:
            return None
    if getattr(job, "file", None):
        try:
            if request:
                return request.build_absolute_uri(job.file.url)
            return job.file.url
        except Exception:
            return None
    return None


class ReportDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportDefinition
        fields = (
            "id",
            "code",
            "name",
            "description",
            "category",
            "handler_key",
            "parameter_schema",
            "supported_formats",
            "supports_scheduling",
            "supports_raw_attachment",
            "supports_charts",
            "required_permission",
            "version",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ReportTemplateSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    definition_code = serializers.SerializerMethodField(read_only=True)
    definition_name = serializers.SerializerMethodField(read_only=True)
    report_type = serializers.SerializerMethodField(read_only=True)
    created_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportTemplate
        fields = (
            "id",
            "organization",
            "organization_name",
            "definition",
            "definition_code",
            "definition_name",
            "name",
            "code",
            "description",
            "config",
            "branding_config",
            "is_default",
            "version",
            "report_type",
            "is_active",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "organization_name",
            "definition_code",
            "definition_name",
            "report_type",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        )

    def get_definition_code(self, obj):
        return getattr(obj.definition, "code", None)

    def get_definition_name(self, obj):
        return getattr(obj.definition, "name", None)

    def get_report_type(self, obj):
        return obj.report_type

    def get_created_by_name(self, obj):
        if not obj.created_by_id:
            return None
        return getattr(obj.created_by, "full_name", None) or getattr(obj.created_by, "username", None) or getattr(obj.created_by, "email", None)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization = attrs.get("organization", getattr(self.instance, "organization", None))
        if organization and hasattr(organization, "id"):
            organization_id = organization.id
        else:
            organization_id = organization
        if organization_id and not _user_can_access_organization(user, organization_id):
            raise serializers.ValidationError({"organization": "You do not have access to this organization."})

        config = attrs.get("config", getattr(self.instance, "config", None))
        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "Config must be a dictionary/object."})
        if config and config.get("report_type"):
            normalized = normalize_report_type(config.get("report_type"))
            if not normalized:
                raise serializers.ValidationError({"config": "Unsupported report_type in template config."})
            attrs["config"] = {**config, "report_type": normalized}

        definition = attrs.get("definition", getattr(self.instance, "definition", None))
        if definition and isinstance(definition, ReportDefinition):
            config_report_type = attrs["config"].get("report_type") if isinstance(attrs.get("config"), dict) else None
            if config_report_type and config_report_type != definition.code:
                raise serializers.ValidationError({"config": "Config report_type must match the selected definition."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            validated_data.setdefault("created_by", request.user)
        return super().create(validated_data)


class _ReportJobBaseSerializer(serializers.ModelSerializer):
    organization_name = serializers.SerializerMethodField(read_only=True)
    data_center_name = serializers.SerializerMethodField(read_only=True)
    definition_code = serializers.SerializerMethodField(read_only=True)
    definition_version = serializers.SerializerMethodField(read_only=True)
    template_name = serializers.SerializerMethodField(read_only=True)
    template_code = serializers.SerializerMethodField(read_only=True)
    requested_by_name = serializers.SerializerMethodField(read_only=True)
    file_url = serializers.SerializerMethodField(read_only=True)
    duration_seconds = serializers.SerializerMethodField(read_only=True)
    is_downloadable = serializers.BooleanField(read_only=True)
    can_retry = serializers.BooleanField(read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    status = serializers.SerializerMethodField(read_only=True)
    report_type = serializers.SerializerMethodField(read_only=True)
    legacy_status = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportJob
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "definition",
            "definition_code",
            "definition_version",
            "template",
            "template_name",
            "template_code",
            "schedule",
            "trigger_source",
            "scheduled_for",
            "requested_by",
            "requested_by_name",
            "status",
            "legacy_status",
            "progress_percent",
            "current_stage",
            "queued_at",
            "started_at",
            "completed_at",
            "retry_count",
            "parent_job",
            "definition_code_snapshot",
            "definition_version_snapshot",
            "template_name_snapshot",
            "template_version_snapshot",
            "template_config_snapshot",
            "parameters",
            "parameters_snapshot",
            "scope_snapshot",
            "recipient_snapshot",
            "output_config_snapshot",
            "file",
            "file_url",
            "error_code",
            "error_message",
            "duration_seconds",
            "is_downloadable",
            "can_retry",
            "can_cancel",
            "report_type",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "organization_name",
            "data_center_name",
            "definition_code",
            "definition_version",
            "template_name",
            "template_code",
            "schedule",
            "trigger_source",
            "scheduled_for",
            "requested_by",
            "requested_by_name",
            "status",
            "legacy_status",
            "progress_percent",
            "current_stage",
            "queued_at",
            "started_at",
            "completed_at",
            "retry_count",
            "parent_job",
            "definition_code_snapshot",
            "definition_version_snapshot",
            "template_name_snapshot",
            "template_version_snapshot",
            "template_config_snapshot",
            "parameters_snapshot",
            "scope_snapshot",
            "recipient_snapshot",
            "output_config_snapshot",
            "file",
            "file_url",
            "error_code",
            "error_message",
            "duration_seconds",
            "is_downloadable",
            "can_retry",
            "can_cancel",
            "report_type",
            "created_at",
            "updated_at",
        )

    def get_requested_by_name(self, obj):
        if not obj.requested_by_id:
            return None
        return getattr(obj.requested_by, "full_name", None) or getattr(obj.requested_by, "username", None) or getattr(obj.requested_by, "email", None)

    def get_status(self, obj):
        return obj.legacy_status

    def get_organization_name(self, obj):
        return getattr(obj.organization, "name", None)

    def get_data_center_name(self, obj):
        return getattr(obj.data_center, "name", None)

    def get_definition_code(self, obj):
        return getattr(obj.definition, "code", None)

    def get_definition_version(self, obj):
        return getattr(obj.definition, "version", None)

    def get_template_name(self, obj):
        return getattr(obj.template, "name", None)

    def get_template_code(self, obj):
        return getattr(obj.template, "code", None)

    def get_file_url(self, obj):
        request = self.context.get("request")
        return _primary_artifact_url(obj, request=request)

    def get_duration_seconds(self, obj):
        return obj.duration_seconds

    def get_report_type(self, obj):
        return obj.report_type

    def get_legacy_status(self, obj):
        return obj.legacy_status

    def update(self, instance, validated_data):
        parameters_changed = (
            "parameters" in validated_data
            and validated_data["parameters"] != instance.parameters
        )

        if parameters_changed:
            if instance.status == ReportJobStatus.PROCESSING:
                raise serializers.ValidationError(
                    {"parameters": "Processing report jobs cannot be edited."}
                )

            if instance.file:
                instance.file.delete(save=False)
                instance.file = None

            if instance.status in {
                ReportJobStatus.COMPLETED,
                ReportJobStatus.FAILED,
                ReportJobStatus.CANCELLED,
                ReportJobStatus.SUCCEEDED,
                ReportJobStatus.PARTIALLY_SUCCEEDED,
            }:
                instance.status = ReportJobStatus.PENDING
                instance.started_at = None
                instance.completed_at = None
                instance.error_message = ""

        return super().update(instance, validated_data)


class ReportJobListSerializer(_ReportJobBaseSerializer):
    class Meta(_ReportJobBaseSerializer.Meta):
        pass


class ReportJobDetailSerializer(_ReportJobBaseSerializer):
    template_config = serializers.SerializerMethodField(read_only=True)
    primary_artifact_id = serializers.SerializerMethodField(read_only=True)
    primary_artifact_status = serializers.SerializerMethodField(read_only=True)
    primary_artifact_format = serializers.SerializerMethodField(read_only=True)

    class Meta(_ReportJobBaseSerializer.Meta):
        fields = _ReportJobBaseSerializer.Meta.fields + (
            "template_config",
            "primary_artifact_id",
            "primary_artifact_status",
            "primary_artifact_format",
        )
        read_only_fields = _ReportJobBaseSerializer.Meta.read_only_fields + (
            "template_config",
            "primary_artifact_id",
            "primary_artifact_status",
            "primary_artifact_format",
        )

    def get_template_config(self, obj):
        return obj.template.config if obj.template_id else {}

    def get_primary_artifact_id(self, obj):
        artifact = getattr(obj, "primary_artifact", None)
        return str(artifact.id) if artifact else None

    def get_primary_artifact_status(self, obj):
        artifact = getattr(obj, "primary_artifact", None)
        return getattr(artifact, "status", None)

    def get_primary_artifact_format(self, obj):
        artifact = getattr(obj, "primary_artifact", None)
        return getattr(artifact, "format", None)


class ReportJobCreateSerializer(_ReportJobBaseSerializer):
    class Meta(_ReportJobBaseSerializer.Meta):
        read_only_fields = (
            "id",
            "organization_name",
            "data_center_name",
            "definition_code",
            "definition_version",
            "template_name",
            "template_code",
            "schedule",
            "trigger_source",
            "scheduled_for",
            "requested_by",
            "requested_by_name",
            "status",
            "legacy_status",
            "progress_percent",
            "current_stage",
            "queued_at",
            "started_at",
            "completed_at",
            "retry_count",
            "parent_job",
            "definition_code_snapshot",
            "definition_version_snapshot",
            "template_name_snapshot",
            "template_version_snapshot",
            "template_config_snapshot",
            "parameters_snapshot",
            "scope_snapshot",
            "recipient_snapshot",
            "output_config_snapshot",
            "file",
            "file_url",
            "error_code",
            "error_message",
            "duration_seconds",
            "is_downloadable",
            "can_retry",
            "can_cancel",
            "report_type",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)

        organization = attrs.get("organization")
        data_center = attrs.get("data_center")
        template = attrs.get("template")
        schedule = attrs.get("schedule")
        parameters = attrs.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise serializers.ValidationError({"parameters": "Parameters must be a dictionary/object."})

        organization_id = organization.id if isinstance(organization, Organization) else organization
        if organization_id and not _user_can_access_organization(user, organization_id):
            raise serializers.ValidationError({"organization": "You do not have access to this organization."})
        if data_center and not _user_can_access_data_center(user, data_center.id if hasattr(data_center, "id") else data_center):
            raise serializers.ValidationError({"data_center": "You do not have access to this data center."})
        if template and organization_id and template.organization_id not in {None, organization_id}:
            raise serializers.ValidationError({"template": "Template must belong to the selected organization or be global."})
        if schedule and organization_id and schedule.organization_id != organization_id:
            raise serializers.ValidationError({"schedule": "Schedule must belong to the selected organization."})

        report_type = getattr(template, "report_type", None) or parameters.get("report_type")
        if template and template.report_type and parameters.get("report_type") and parameters["report_type"] != template.report_type:
            raise serializers.ValidationError({"parameters": "Parameters report_type must match the selected template."})
        if schedule and schedule.definition_id and report_type and schedule.definition.code != report_type:
            raise serializers.ValidationError({"schedule": "Schedule definition must match the selected report type."})
        if not report_type:
            raise serializers.ValidationError({"template": "A report_type must be provided through the template config or parameters."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            validated_data["requested_by"] = request.user
        from apps.reports.services.jobs import ReportJobService

        template = validated_data["template"]
        definition = validated_data.get("definition")
        if not definition and getattr(template, "definition", None):
            definition = template.definition
        if not definition:
            report_type = validated_data.get("report_type")
            if not report_type:
                parameters = validated_data.get("parameters") or {}
                report_type = parameters.get("report_type")
            if report_type and getattr(template, "definition", None) and template.definition.code == report_type:
                definition = template.definition
        if not definition:
            raise serializers.ValidationError({"definition": "A compatible report definition could not be resolved."})
        primary_format = validated_data.get("primary_format")
        if not primary_format:
            config = template.config if isinstance(template.config, dict) else {}
            primary_format = config.get("output_format") or config.get("primary_format")
        if not primary_format:
            supported_formats = getattr(template.definition, "supported_formats", []) if getattr(template, "definition", None) else []
            primary_format = supported_formats[0] if supported_formats else "CSV"

        return ReportJobService.create_manual_job(
            organization=validated_data["organization"],
            data_center=validated_data.get("data_center"),
            definition=definition,
            template=validated_data["template"],
            parameters=validated_data.get("parameters", {}),
            primary_format=primary_format,
            attachment_formats=validated_data.get("attachment_formats", []),
            requested_by=validated_data.get("requested_by"),
            recipients=validated_data.get("recipients", []),
            enqueue=True,
        )


class ReportJobGenerateSerializer(serializers.Serializer):
    class Meta:
        fields = ()


class ReportJobRetrySerializer(serializers.Serializer):
    class Meta:
        fields = ()


class ReportScheduleRunNowSerializer(serializers.Serializer):
    class Meta:
        fields = ()


class ReportScheduleRecipientSerializer(serializers.ModelSerializer):
    schedule_name = serializers.CharField(source="schedule.name", read_only=True)
    user_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportScheduleRecipient
        fields = (
            "id",
            "schedule",
            "schedule_name",
            "channel",
            "recipient_type",
            "destination",
            "display_name",
            "user",
            "user_name",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "schedule_name", "user_name", "created_at", "updated_at")

    def get_user_name(self, obj):
        if not obj.user_id:
            return None
        return getattr(obj.user, "full_name", None) or getattr(obj.user, "username", None) or getattr(obj.user, "email", None)


class ReportArtifactSerializer(serializers.ModelSerializer):
    job_reference = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportArtifact
        fields = (
            "id",
            "job",
            "job_reference",
            "artifact_type",
            "format",
            "file_name",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "status",
            "expires_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "job_reference",
            "checksum_sha256",
            "size_bytes",
            "created_at",
            "updated_at",
        )

    def get_job_reference(self, obj):
        return str(obj.job_id) if obj.job_id else None


class ReportDeliverySerializer(serializers.ModelSerializer):
    recipient_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportDelivery
        fields = (
            "id",
            "job",
            "schedule",
            "artifact",
            "channel",
            "recipient",
            "recipient_name",
            "recipient_type",
            "destination_snapshot",
            "attempt_number",
            "status",
            "provider_message_id",
            "provider_response",
            "queued_at",
            "attempted_at",
            "accepted_at",
            "delivered_at",
            "failed_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "recipient_name", "created_at", "updated_at")

    def get_recipient_name(self, obj):
        if not obj.recipient_id:
            return None
        recipient = obj.recipient
        return recipient.display_name or recipient.destination


class ReportScheduleSerializer(serializers.ModelSerializer):
    report_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    frequency = serializers.CharField()
    output_format = serializers.CharField()
    delivery_time = serializers.CharField()
    organization_name = serializers.SerializerMethodField(read_only=True)
    data_center_name = serializers.SerializerMethodField(read_only=True)
    created_by_name = serializers.SerializerMethodField(read_only=True)
    definition_code = serializers.SerializerMethodField(read_only=True)
    definition_name = serializers.SerializerMethodField(read_only=True)
    template_name = serializers.SerializerMethodField(read_only=True)
    template_code = serializers.SerializerMethodField(read_only=True)
    report_type_label = serializers.SerializerMethodField(read_only=True)
    frequency_label = serializers.CharField(source="get_frequency_display", read_only=True)
    output_format_label = serializers.CharField(source="get_output_format_display", read_only=True)
    recipient_count = serializers.SerializerMethodField(read_only=True)
    active_recipient_count = serializers.SerializerMethodField(read_only=True)
    last_job_status = serializers.SerializerMethodField(read_only=True)
    last_job_file_url = serializers.SerializerMethodField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ReportSchedule
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "definition",
            "definition_code",
            "definition_name",
            "template",
            "template_name",
            "template_code",
            "name",
            "report_type",
            "report_type_label",
            "frequency",
            "frequency_label",
            "delivery_time",
            "output_format",
            "output_format_label",
            "primary_format",
            "attachment_formats",
            "recurrence_rule",
            "status",
            "status_label",
            "start_at",
            "end_at",
            "parameters",
            "recipients",
            "send_sms",
            "sms_recipients",
            "recipient_count",
            "active_recipient_count",
            "attach_raw_data",
            "is_active",
            "next_run_at",
            "last_run_at",
            "last_success_at",
            "last_sent_at",
            "consecutive_failure_count",
            "last_delivery_status",
            "last_error_message",
            "last_job",
            "last_job_status",
            "last_job_file_url",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "organization_name",
            "data_center_name",
            "definition_code",
            "definition_name",
            "template_name",
            "template_code",
            "report_type_label",
            "frequency_label",
            "output_format_label",
            "primary_format",
            "attachment_formats",
            "recurrence_rule",
            "status",
            "status_label",
            "start_at",
            "end_at",
            "recipient_count",
            "active_recipient_count",
            "next_run_at",
            "last_run_at",
            "last_success_at",
            "last_sent_at",
            "consecutive_failure_count",
            "last_delivery_status",
            "last_error_message",
            "last_job",
            "last_job_status",
            "last_job_file_url",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        )

    def get_organization_name(self, obj):
        return getattr(obj.organization, "name", None)

    def get_data_center_name(self, obj):
        return getattr(obj.data_center, "name", None)

    def get_created_by_name(self, obj):
        if not obj.created_by_id:
            return None
        return getattr(obj.created_by, "full_name", None) or getattr(obj.created_by, "username", None) or getattr(obj.created_by, "email", None)

    def get_definition_code(self, obj):
        return getattr(obj.definition, "code", None)

    def get_definition_name(self, obj):
        return getattr(obj.definition, "name", None)

    def get_template_name(self, obj):
        return getattr(obj.template, "name", None)

    def get_template_code(self, obj):
        return getattr(obj.template, "code", None)

    def get_report_type_label(self, obj):
        if obj.definition_id:
            return obj.definition.name
        return REPORT_TYPE_LABELS.get(obj.report_type, obj.report_type)

    def get_recipient_count(self, obj):
        email_recipients = obj.recipients if isinstance(obj.recipients, list) else []
        sms_recipients = obj.sms_recipients if obj.send_sms and isinstance(obj.sms_recipients, list) else []
        if obj.pk and hasattr(obj, "recipient_entries"):
            try:
                return len([entry for entry in obj.recipient_entries.all() if entry.is_active])
            except Exception:
                pass
        return len({str(value).strip() for value in [*email_recipients, *sms_recipients] if str(value).strip()})

    def get_active_recipient_count(self, obj):
        if obj.pk and hasattr(obj, "recipient_entries"):
            try:
                return len([entry for entry in obj.recipient_entries.all() if entry.is_active])
            except Exception:
                return 0
        return self.get_recipient_count(obj)

    def get_last_job_status(self, obj):
        if not obj.last_job_id:
            return None
        return getattr(obj.last_job, "legacy_status", None) or getattr(obj.last_job, "status", None)

    def get_last_job_file_url(self, obj):
        if not obj.last_job_id:
            return None
        request = self.context.get("request")
        return _primary_artifact_url(obj.last_job, request=request)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)

        organization = attrs.get("organization", getattr(self.instance, "organization", None))
        data_center = attrs.get("data_center", getattr(self.instance, "data_center", None))
        definition = attrs.get("definition", getattr(self.instance, "definition", None))
        template = attrs.get("template", getattr(self.instance, "template", None))
        organization_id = organization.id if hasattr(organization, "id") else organization
        data_center_id = data_center.id if hasattr(data_center, "id") else data_center

        if organization_id and not _user_can_access_organization(user, organization_id):
            raise serializers.ValidationError({"organization": "You do not have access to this organization."})
        if data_center_id and not _user_can_access_data_center(user, data_center_id):
            raise serializers.ValidationError({"data_center": "You do not have access to this data center."})

        report_type = attrs.get("report_type", getattr(self.instance, "report_type", None))
        normalized_report_type = normalize_report_type(report_type) if report_type else None
        if report_type and not normalized_report_type:
            raise serializers.ValidationError({"report_type": "Unsupported report type."})
        if normalized_report_type:
            attrs["report_type"] = normalized_report_type

        frequency = attrs.get("frequency", getattr(self.instance, "frequency", None))
        normalized_frequency = normalize_report_frequency(frequency)
        if not normalized_frequency:
            raise serializers.ValidationError({"frequency": "Unsupported frequency."})
        attrs["frequency"] = normalized_frequency

        output_format = attrs.get("output_format", getattr(self.instance, "output_format", None))
        normalized_format = normalize_report_format(output_format)
        if not normalized_format:
            raise serializers.ValidationError({"output_format": "Unsupported report format."})
        attrs["output_format"] = normalized_format

        delivery_time = attrs.get("delivery_time", getattr(self.instance, "delivery_time", None))
        normalized_delivery_time = _normalize_delivery_time(delivery_time)
        if normalized_delivery_time is None:
            raise serializers.ValidationError({
                "delivery_time": "Unsupported delivery time. Use HH:MM, HH:MM:SS, or a 12-hour time like 06:00 AM."
            })
        attrs["delivery_time"] = normalized_delivery_time

        parameters = attrs.get("parameters", getattr(self.instance, "parameters", {}))
        if not isinstance(parameters, dict):
            raise serializers.ValidationError({"parameters": "Parameters must be a JSON object."})
        attrs["parameters"] = parameters

        attachment_formats = attrs.get("attachment_formats", getattr(self.instance, "attachment_formats", []))
        if not isinstance(attachment_formats, list):
            raise serializers.ValidationError({"attachment_formats": "Attachment formats must be a list."})
        attrs["attachment_formats"] = attachment_formats

        recurrence_rule = attrs.get("recurrence_rule", getattr(self.instance, "recurrence_rule", {}))
        if not isinstance(recurrence_rule, dict):
            raise serializers.ValidationError({"recurrence_rule": "Recurrence rule must be a JSON object."})
        attrs["recurrence_rule"] = recurrence_rule

        send_sms = attrs.get("send_sms", getattr(self.instance, "send_sms", False))
        sms_recipients = attrs.get("sms_recipients", getattr(self.instance, "sms_recipients", []))
        if not isinstance(sms_recipients, list):
            raise serializers.ValidationError({"sms_recipients": "SMS recipients must be a list."})
        normalized_sms_recipients = list(dict.fromkeys(str(value).strip() for value in sms_recipients if str(value).strip()))
        if send_sms and not normalized_sms_recipients:
            raise serializers.ValidationError({"sms_recipients": "At least one SMS recipient is required when SMS is enabled."})
        attrs["send_sms"] = bool(send_sms)
        attrs["sms_recipients"] = normalized_sms_recipients

        recipients = attrs.get("recipients", getattr(self.instance, "recipients", []))
        if not isinstance(recipients, list):
            raise serializers.ValidationError({"recipients": "Recipients must be a list of email addresses."})
        normalized_recipients = []
        seen = set()
        for recipient in recipients:
            candidate = str(recipient).strip().lower()
            if not candidate:
                continue
            if candidate.count("@") != 1:
                raise serializers.ValidationError({"recipients": f"Invalid recipient email: {candidate}"})
            local_part, domain_part = candidate.split("@", 1)
            if not local_part or not domain_part:
                raise serializers.ValidationError({"recipients": f"Invalid recipient email: {candidate}"})
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized_recipients.append(candidate)
        if not normalized_recipients and not (send_sms and normalized_sms_recipients):
            raise serializers.ValidationError({"recipients": "At least one recipient email is required."})
        attrs["recipients"] = normalized_recipients

        if definition and template and template.definition_id and template.definition_id != definition.id:
            raise serializers.ValidationError({"template": "Template definition must match the selected definition."})
        if definition and normalized_report_type and definition.code != normalized_report_type:
            raise serializers.ValidationError({"definition": "Definition must match the selected report type."})
        if template and normalized_report_type and template.report_type and template.report_type != normalized_report_type:
            raise serializers.ValidationError({"template": "Template report type must match the selected report type."})
        if not definition and template and template.definition_id:
            attrs["definition"] = template.definition
            definition = template.definition
        if not definition and normalized_report_type:
            try:
                from apps.reports.models import ReportDefinition

                definition = ReportDefinition.objects.get(code=normalized_report_type)
                attrs["definition"] = definition
            except ReportDefinition.DoesNotExist:
                raise serializers.ValidationError({"definition": "A matching report definition could not be resolved."})
        if not definition:
            raise serializers.ValidationError({"definition": "A report definition is required."})

        status_value = attrs.get("status", getattr(self.instance, "status", None))
        if status_value and status_value not in ReportScheduleStatus.values:
            raise serializers.ValidationError({"status": "Unsupported schedule status."})

        created_by = attrs.get("created_by", getattr(self.instance, "created_by", None))
        if created_by is None and user and user.is_authenticated:
            attrs["created_by"] = user
        return attrs
