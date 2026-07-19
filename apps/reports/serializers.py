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
from .models import ReportJob, ReportJobStatus, ReportSchedule, ReportTemplate


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


class ReportTemplateSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    report_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportTemplate
        fields = (
            "id",
            "organization",
            "organization_name",
            "name",
            "code",
            "description",
            "config",
            "report_type",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "organization_name", "report_type", "created_at", "updated_at")

    def get_report_type(self, obj):
        return obj.report_type

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization_id = attrs.get("organization", getattr(self.instance, "organization", None))
        if organization_id and hasattr(organization_id, "id"):
            organization_id = organization_id.id
        if organization_id and not _user_can_access_organization(user, organization_id):
            raise serializers.ValidationError({"organization": "You do not have access to this organization."})

        config = attrs.get("config", getattr(self.instance, "config", None))
        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "Config must be a dictionary/object."})
        if not config.get("report_type"):
            raise serializers.ValidationError({"config": "Config must include report_type."})
        return attrs


class _ReportJobBaseSerializer(serializers.ModelSerializer):
    organization_name = serializers.SerializerMethodField(read_only=True)
    data_center_name = serializers.SerializerMethodField(read_only=True)
    template_name = serializers.SerializerMethodField(read_only=True)
    template_code = serializers.SerializerMethodField(read_only=True)
    requested_by_name = serializers.SerializerMethodField(read_only=True)
    file_url = serializers.SerializerMethodField(read_only=True)
    duration_seconds = serializers.SerializerMethodField(read_only=True)
    is_downloadable = serializers.BooleanField(read_only=True)
    can_retry = serializers.BooleanField(read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    report_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportJob
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "template",
            "template_name",
            "template_code",
            "requested_by",
            "requested_by_name",
            "status",
            "parameters",
            "file",
            "file_url",
            "started_at",
            "completed_at",
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
            "template_name",
            "template_code",
            "requested_by",
            "requested_by_name",
            "status",
            "file",
            "file_url",
            "started_at",
            "completed_at",
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

    def get_organization_name(self, obj):
        return getattr(obj.organization, "name", None)

    def get_data_center_name(self, obj):
        return getattr(obj.data_center, "name", None)

    def get_template_name(self, obj):
        return getattr(obj.template, "name", None)

    def get_template_code(self, obj):
        return getattr(obj.template, "code", None)

    def get_file_url(self, obj):
        if not obj.file:
            return None
        try:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        except Exception:
            return None

    def get_duration_seconds(self, obj):
        return obj.duration_seconds

    def get_report_type(self, obj):
        return obj.report_type

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

    class Meta(_ReportJobBaseSerializer.Meta):
        fields = _ReportJobBaseSerializer.Meta.fields + ("template_config",)
        read_only_fields = _ReportJobBaseSerializer.Meta.read_only_fields + ("template_config",)

    def get_template_config(self, obj):
        return obj.template.config if obj.template_id else {}


class ReportJobCreateSerializer(_ReportJobBaseSerializer):
    class Meta(_ReportJobBaseSerializer.Meta):
        read_only_fields = (
            "id",
            "organization_name",
            "data_center_name",
            "template_name",
            "template_code",
            "requested_by_name",
            "status",
            "file",
            "file_url",
            "started_at",
            "completed_at",
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
        parameters = attrs.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise serializers.ValidationError({"parameters": "Parameters must be a dictionary/object."})

        organization_id = organization.id if isinstance(organization, Organization) else organization
        if organization_id and not _user_can_access_organization(user, organization_id):
            raise serializers.ValidationError({"organization": "You do not have access to this organization."})
        if data_center and not _user_can_access_data_center(user, data_center.id if hasattr(data_center, "id") else data_center):
            raise serializers.ValidationError({"data_center": "You do not have access to this data center."})
        if template and template.organization_id != organization_id:
            raise serializers.ValidationError({"template": "Template must belong to the selected organization."})

        report_type = getattr(template, "report_type", None) or parameters.get("report_type")
        if template and template.report_type and parameters.get("report_type") and parameters["report_type"] != template.report_type:
            raise serializers.ValidationError({"parameters": "Parameters report_type must match the selected template."})
        if not report_type:
            raise serializers.ValidationError({"template": "A report_type must be provided through the template config or parameters."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            validated_data["requested_by"] = request.user
        return super().create(validated_data)


class ReportJobGenerateSerializer(serializers.Serializer):
    class Meta:
        fields = ()


class ReportJobRetrySerializer(serializers.Serializer):
    class Meta:
        fields = ()


class ReportScheduleRunNowSerializer(serializers.Serializer):
    class Meta:
        fields = ()


class ReportScheduleSerializer(serializers.ModelSerializer):
    report_type = serializers.CharField()
    frequency = serializers.CharField()
    output_format = serializers.CharField()
    delivery_time = serializers.CharField()
    organization_name = serializers.SerializerMethodField(read_only=True)
    data_center_name = serializers.SerializerMethodField(read_only=True)
    created_by_name = serializers.SerializerMethodField(read_only=True)
    report_type_label = serializers.SerializerMethodField(read_only=True)
    frequency_label = serializers.CharField(source="get_frequency_display", read_only=True)
    output_format_label = serializers.CharField(source="get_output_format_display", read_only=True)
    recipient_count = serializers.SerializerMethodField(read_only=True)
    last_job_status = serializers.SerializerMethodField(read_only=True)
    last_job_file_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReportSchedule
        fields = (
            "id",
            "organization",
            "organization_name",
            "data_center",
            "data_center_name",
            "name",
            "report_type",
            "report_type_label",
            "frequency",
            "frequency_label",
            "delivery_time",
            "output_format",
            "output_format_label",
            "parameters",
            "recipients",
            "send_sms",
            "sms_recipients",
            "recipient_count",
            "attach_raw_data",
            "is_active",
            "next_run_at",
            "last_run_at",
            "last_sent_at",
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
            "report_type_label",
            "frequency_label",
            "output_format_label",
            "recipient_count",
            "next_run_at",
            "last_run_at",
            "last_sent_at",
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

    def get_report_type_label(self, obj):
        return REPORT_TYPE_LABELS.get(obj.report_type, obj.report_type)

    def get_recipient_count(self, obj):
        recipients = obj.recipients if isinstance(obj.recipients, list) else []
        sms_recipients = obj.sms_recipients if obj.send_sms and isinstance(obj.sms_recipients, list) else []
        return len({str(value).strip() for value in [*recipients, *sms_recipients] if str(value).strip()})

    def get_last_job_status(self, obj):
        if not obj.last_job_id:
            return None
        return getattr(obj.last_job, "status", None)

    def get_last_job_file_url(self, obj):
        if not obj.last_job_id or not getattr(obj.last_job, "file", None):
            return None
        try:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.last_job.file.url)
            return obj.last_job.file.url
        except Exception:
            return None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)

        organization = attrs.get("organization", getattr(self.instance, "organization", None))
        data_center = attrs.get("data_center", getattr(self.instance, "data_center", None))
        organization_id = organization.id if hasattr(organization, "id") else organization
        data_center_id = data_center.id if hasattr(data_center, "id") else data_center

        if organization_id and not _user_can_access_organization(user, organization_id):
            raise serializers.ValidationError({"organization": "You do not have access to this organization."})
        if data_center_id and not _user_can_access_data_center(user, data_center_id):
            raise serializers.ValidationError({"data_center": "You do not have access to this data center."})

        report_type = attrs.get("report_type", getattr(self.instance, "report_type", None))
        normalized_report_type = normalize_report_type(report_type)
        if not normalized_report_type:
            raise serializers.ValidationError({"report_type": "Unsupported report type."})
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

        created_by = attrs.get("created_by", getattr(self.instance, "created_by", None))
        if created_by is None and user and user.is_authenticated:
            attrs["created_by"] = user
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated and validated_data.get("created_by") is None:
            validated_data["created_by"] = user
        return super().create(validated_data)
