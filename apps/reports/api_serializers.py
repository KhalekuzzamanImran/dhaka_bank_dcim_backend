from __future__ import annotations

from datetime import datetime, timedelta
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.common.access import get_access_scope
from apps.datacenters.models import DataCenter
from apps.organizations.models import Organization

from .domain import (
    ReportArtifactStatus,
    ReportArtifactType,
    ReportDeliveryStatus,
    ReportJobStatus,
    ReportJobTriggerSource,
    ReportRecipientChannel,
    ReportRecipientType,
    ReportScheduleStatus,
)
from .models import (
    ReportArtifact,
    ReportDelivery,
    ReportDefinition,
    ReportJob,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportTemplate,
)
from .services.jobs import ReportJobService
from .services.parameters import normalize_report_parameters
from .services.permissions import get_schedule_allowed_actions, validate_report_scope
from .scheduling.calculator import calculate_next_runs


def _display_name(user: User | None) -> str | None:
    if not user:
        return None
    return getattr(user, "full_name", None) or getattr(user, "username", None) or getattr(user, "email", None)


def _mask_email(value: str) -> str:
    candidate = str(value or "").strip()
    if "@" not in candidate:
        return candidate
    local_part, domain_part = candidate.split("@", 1)
    if not local_part:
        return f"***@{domain_part}"
    if len(local_part) <= 2:
        masked_local = f"{local_part[0]}***"
    else:
        masked_local = f"{local_part[:2]}***"
    return f"{masked_local}@{domain_part}"


def _mask_phone(value: str) -> str:
    candidate = str(value or "").strip()
    if len(candidate) <= 4:
        return "***"
    return f"{candidate[:4]}{'*' * max(0, len(candidate) - 6)}{candidate[-2:]}"


def _mask_destination(channel: str, value: str) -> str:
    if channel == ReportRecipientChannel.EMAIL:
        return _mask_email(value)
    if channel == ReportRecipientChannel.SMS:
        return _mask_phone(value)
    return str(value or "")


def _safe_datetime_display(value):
    if not value:
        return None
    try:
        local_value = timezone.localtime(value)
    except Exception:
        local_value = value
    return local_value.strftime("%d %b %Y, %I:%M %p")


def _normalize_recipient_payload(recipient: dict) -> dict:
    channel = str(recipient.get("channel") or "").upper()
    recipient_type = str(recipient.get("recipient_type") or ReportRecipientType.TO).upper()
    destination = str(recipient.get("destination") or "").strip()
    display_name = recipient.get("display_name")
    if channel == ReportRecipientChannel.EMAIL:
        destination = destination.lower()
    return {
        "channel": channel,
        "recipient_type": recipient_type,
        "destination": destination,
        "display_name": display_name,
    }


def _recipient_values(recipients):
    normalized = []
    seen = set()
    for recipient in recipients or []:
        if isinstance(recipient, ReportScheduleRecipient):
            payload = {
                "channel": recipient.channel,
                "recipient_type": recipient.recipient_type,
                "destination": recipient.destination,
                "display_name": recipient.display_name,
            }
        elif isinstance(recipient, dict):
            payload = _normalize_recipient_payload(recipient)
        else:
            continue
        key = (payload["channel"], payload["recipient_type"], payload["destination"].lower() if payload["channel"] == ReportRecipientChannel.EMAIL else payload["destination"])
        if key in seen or not payload["destination"]:
            continue
        seen.add(key)
        normalized.append(payload)
    return normalized


class OrganizationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name", "code")
        read_only_fields = fields


class DataCenterSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DataCenter
        fields = ("id", "name", "code", "organization")
        read_only_fields = fields


class UserSummarySerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "display_name")
        read_only_fields = fields

    def get_display_name(self, obj):
        return _display_name(obj)


class ReportDefinitionListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportDefinition
        fields = (
            "id",
            "code",
            "name",
            "description",
            "category",
            "supported_formats",
            "supports_scheduling",
            "supports_raw_attachment",
            "supports_charts",
            "required_permission",
            "version",
            "is_active",
        )
        read_only_fields = fields


class ReportDefinitionDetailSerializer(ReportDefinitionListSerializer):
    class Meta(ReportDefinitionListSerializer.Meta):
        fields = ReportDefinitionListSerializer.Meta.fields + ("parameter_schema",)
        read_only_fields = fields


class ReportTemplateListSerializer(serializers.ModelSerializer):
    organization_summary = OrganizationSummarySerializer(source="organization", read_only=True)
    definition_summary = ReportDefinitionListSerializer(source="definition", read_only=True)

    class Meta:
        model = ReportTemplate
        fields = (
            "id",
            "code",
            "name",
            "description",
            "definition",
            "definition_summary",
            "organization",
            "organization_summary",
            "version",
            "is_default",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ReportTemplateDetailSerializer(ReportTemplateListSerializer):
    created_by_summary = UserSummarySerializer(source="created_by", read_only=True)

    class Meta(ReportTemplateListSerializer.Meta):
        fields = ReportTemplateListSerializer.Meta.fields + (
            "config",
            "branding_config",
            "created_by",
            "created_by_summary",
        )
        read_only_fields = fields


class ReportTemplateWriteSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    definition_code = serializers.CharField(write_only=True)

    class Meta:
        model = ReportTemplate
        fields = (
            "organization_id",
            "definition_code",
            "name",
            "code",
            "description",
            "config",
            "branding_config",
            "is_default",
            "is_active",
        )

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization_id = attrs.pop("organization_id", None)
        definition_code = attrs.pop("definition_code", None)

        definition = ReportDefinition.objects.filter(code=definition_code).first()
        if not definition:
            raise serializers.ValidationError({"definition_code": "Unsupported report definition."})
        if not definition.is_active:
            raise serializers.ValidationError({"definition_code": "Report definition is inactive."})
        if not isinstance(attrs.get("config", {}), dict):
            raise serializers.ValidationError({"config": "Config must be an object."})
        if not isinstance(attrs.get("branding_config", {}), dict):
            raise serializers.ValidationError({"branding_config": "Branding config must be an object."})

        organization = None
        if organization_id:
            organization = Organization.objects.filter(pk=organization_id).first()
            if not organization:
                raise serializers.ValidationError({"organization_id": "Organization does not exist."})
            validate_report_scope(user=user, organization=organization)
        elif not getattr(user, "is_superuser", False):
            raise serializers.ValidationError({"organization_id": "Global templates can only be managed by system administrators."})

        config = dict(attrs.get("config") or {})
        if config.get("report_type") and config.get("report_type") != definition.code:
            raise serializers.ValidationError({"config": "Config report_type must match the selected definition."})
        config["report_type"] = definition.code
        attrs["config"] = config
        attrs["organization"] = organization
        attrs["definition"] = definition
        attrs.setdefault("version", None)
        return attrs

    def create(self, validated_data):
        validated_data.pop("version", None)
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            validated_data.setdefault("created_by", request.user)
        instance = ReportTemplate(**validated_data)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        validated_data.pop("version", None)
        validated_data.pop("organization", None)
        validated_data.pop("definition", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.version = (instance.version or 0) + 1
        instance.save()
        return instance


class _ReportArtifactSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportArtifact
        fields = ("id", "artifact_type", "format", "status", "file_name")
        read_only_fields = fields


class _ReportDeliverySummarySerializer(serializers.ModelSerializer):
    masked_destination = serializers.SerializerMethodField()

    class Meta:
        model = ReportDelivery
        fields = ("id", "channel", "recipient_type", "status", "masked_destination", "attempt_number")
        read_only_fields = fields

    def get_masked_destination(self, obj):
        return _mask_destination(obj.channel, obj.destination_snapshot)


class _ReportJobSummarySerializer(serializers.ModelSerializer):
    definition_summary = ReportDefinitionListSerializer(source="definition", read_only=True)
    template_summary = ReportTemplateListSerializer(source="template", read_only=True)
    requested_by_summary = UserSummarySerializer(source="requested_by", read_only=True)

    class Meta:
        model = ReportJob
        fields = (
            "id",
            "reference",
            "definition",
            "definition_summary",
            "template",
            "template_summary",
            "status",
            "legacy_status",
            "trigger_source",
            "queued_at",
            "started_at",
            "completed_at",
            "requested_by",
            "requested_by_summary",
        )
        read_only_fields = fields


class ReportJobListSerializer(_ReportJobSummarySerializer):
    organization_summary = OrganizationSummarySerializer(source="organization", read_only=True)
    data_center_summary = DataCenterSummarySerializer(source="data_center", read_only=True)
    latest_artifact = serializers.SerializerMethodField()
    delivery_summary = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    duration_seconds = serializers.IntegerField(read_only=True)

    class Meta(_ReportJobSummarySerializer.Meta):
        fields = _ReportJobSummarySerializer.Meta.fields + (
            "organization",
            "organization_summary",
            "data_center",
            "data_center_summary",
            "scheduled_for",
            "progress_percent",
            "current_stage",
            "retry_count",
            "parent_job",
            "duration_seconds",
            "latest_artifact",
            "delivery_summary",
            "allowed_actions",
        )
        read_only_fields = fields

    def get_latest_artifact(self, obj):
        artifact = None
        artifacts = list(obj.artifacts.all()) if hasattr(obj, "artifacts") else []
        for candidate in artifacts:
            if candidate.artifact_type == ReportArtifactType.PRIMARY and candidate.status == ReportArtifactStatus.AVAILABLE:
                artifact = candidate
                break
        if artifact is None and artifacts:
            artifact = artifacts[0]
        return _ReportArtifactSummarySerializer(artifact, context=self.context).data if artifact else None

    def get_delivery_summary(self, obj):
        deliveries = getattr(obj, "deliveries", None)
        if deliveries is None:
            return {"total": 0, "failed": 0, "delivered": 0, "pending": 0}
        status_counts = {status: 0 for status in ReportDeliveryStatus.values}
        total = 0
        for delivery in deliveries.all():
            total += 1
            if delivery.status in status_counts:
                status_counts[delivery.status] += 1
        return {
            "total": total,
            "failed": status_counts[ReportDeliveryStatus.FAILED],
            "delivered": status_counts[ReportDeliveryStatus.DELIVERED],
            "pending": status_counts[ReportDeliveryStatus.PENDING] + status_counts[ReportDeliveryStatus.SENDING],
            "accepted": status_counts[ReportDeliveryStatus.ACCEPTED],
            "skipped": status_counts[ReportDeliveryStatus.SKIPPED],
        }

    def get_allowed_actions(self, obj):
        actions = []
        if obj.can_retry:
            actions.append("retry")
        if obj.can_cancel:
            actions.append("cancel")
        if obj.is_downloadable:
            actions.append("download")
        return actions


class ReportJobDetailSerializer(ReportJobListSerializer):
    artifacts = _ReportArtifactSummarySerializer(many=True, read_only=True)
    deliveries = _ReportDeliverySummarySerializer(many=True, read_only=True)
    safe_error_code = serializers.CharField(source="error_code", read_only=True)
    safe_error_message = serializers.CharField(source="error_message", read_only=True)

    class Meta(ReportJobListSerializer.Meta):
        fields = ReportJobListSerializer.Meta.fields + (
            "artifacts",
            "deliveries",
            "safe_error_code",
            "safe_error_message",
            "definition_code_snapshot",
            "definition_version_snapshot",
            "template_name_snapshot",
            "template_version_snapshot",
            "output_config_snapshot",
        )
        read_only_fields = fields


class ReportJobCreateSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    data_center_id = serializers.UUIDField(required=False, allow_null=True)
    definition_code = serializers.CharField()
    template_id = serializers.UUIDField()
    parameters = serializers.DictField(required=False, default=dict)
    primary_format = serializers.ChoiceField(choices=["PDF", "CSV", "XLSX"], default="CSV")
    attachment_formats = serializers.ListField(child=serializers.ChoiceField(choices=["PDF", "CSV", "XLSX"]), required=False, default=list)
    recipients = serializers.ListField(child=serializers.DictField(), required=False, default=list)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        organization = Organization.objects.filter(pk=attrs["organization_id"]).first()
        if not organization:
            raise serializers.ValidationError({"organization_id": "Organization does not exist."})
        data_center = None
        if attrs.get("data_center_id"):
            data_center = DataCenter.objects.filter(pk=attrs["data_center_id"]).first()
            if not data_center:
                raise serializers.ValidationError({"data_center_id": "Data center does not exist."})
        validate_report_scope(user=user, organization=organization, data_center=data_center)

        definition = ReportDefinition.objects.filter(code=attrs["definition_code"]).first()
        if not definition:
            raise serializers.ValidationError({"definition_code": "Unsupported report definition."})
        if not definition.is_active:
            raise serializers.ValidationError({"definition_code": "Report definition is inactive."})
        if attrs.get("primary_format") and str(attrs["primary_format"]).upper() not in {str(fmt).upper() for fmt in definition.supported_formats or []}:
            raise serializers.ValidationError({"primary_format": "The selected report definition does not support the requested primary format."})
        template = ReportTemplate.objects.select_related("definition", "organization").filter(pk=attrs["template_id"]).first()
        if not template:
            raise serializers.ValidationError({"template_id": "Template does not exist."})
        if not template.is_active:
            raise serializers.ValidationError({"template_id": "Template is inactive."})
        if template.definition_id and template.definition_id != definition.id:
            raise serializers.ValidationError({"template_id": "Template definition must match the selected report definition."})
        if template.organization_id not in {None, organization.id}:
            raise serializers.ValidationError({"template_id": "Template must belong to the selected organization or be global."})

        normalized_parameters = normalize_report_parameters(attrs.get("parameters") or {})
        if normalized_parameters.get("report_type") and normalized_parameters["report_type"] != definition.code:
            raise serializers.ValidationError({"parameters": "report_type does not match the selected definition."})
        normalized_parameters["report_type"] = definition.code

        normalized_recipients = []
        for recipient in _recipient_values(attrs.get("recipients") or []):
            if recipient["channel"] not in ReportRecipientChannel.values:
                raise serializers.ValidationError({"recipients": "Unsupported recipient channel."})
            if recipient["recipient_type"] not in ReportRecipientType.values:
                raise serializers.ValidationError({"recipients": "Unsupported recipient type."})
            if recipient["channel"] == ReportRecipientChannel.EMAIL and "@" not in recipient["destination"]:
                raise serializers.ValidationError({"recipients": "Invalid email recipient."})
            if recipient["channel"] == ReportRecipientChannel.SMS and not recipient["destination"]:
                raise serializers.ValidationError({"recipients": "Invalid SMS recipient."})
            normalized_recipients.append(recipient)

        attachment_formats = list(dict.fromkeys([fmt.upper() for fmt in attrs.get("attachment_formats") or [] if fmt]))
        supported_formats = {str(fmt).upper() for fmt in definition.supported_formats or []}
        invalid_attachments = [fmt for fmt in attachment_formats if supported_formats and fmt not in supported_formats]
        if invalid_attachments:
            raise serializers.ValidationError({"attachment_formats": f"Unsupported attachment formats: {', '.join(invalid_attachments)}"})

        attrs["organization"] = organization
        attrs["data_center"] = data_center
        attrs["definition"] = definition
        attrs["template"] = template
        attrs["parameters"] = normalized_parameters
        attrs["recipients"] = normalized_recipients
        attrs["attachment_formats"] = attachment_formats
        return attrs


class ReportJobActionSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(required=False, default=True)


class ReportScheduleRecipientSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportScheduleRecipient
        fields = ("id", "channel", "recipient_type", "destination", "display_name", "user", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class _ReportScheduleBaseSerializer(serializers.ModelSerializer):
    organization_summary = OrganizationSummarySerializer(source="organization", read_only=True)
    data_center_summary = DataCenterSummarySerializer(source="data_center", read_only=True)
    definition_summary = ReportDefinitionListSerializer(source="definition", read_only=True)
    template_summary = ReportTemplateListSerializer(source="template", read_only=True)
    created_by_summary = UserSummarySerializer(source="created_by", read_only=True)
    recipient_count = serializers.SerializerMethodField()
    human_readable_recurrence = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    latest_job = serializers.SerializerMethodField()
    latest_delivery = serializers.SerializerMethodField()
    next_runs_preview = serializers.SerializerMethodField()

    class Meta:
        model = ReportSchedule
        fields = (
            "id",
            "name",
            "organization",
            "organization_summary",
            "data_center",
            "data_center_summary",
            "definition",
            "definition_summary",
            "template",
            "template_summary",
            "report_type",
            "frequency",
            "delivery_time",
            "primary_format",
            "attachment_formats",
            "output_format",
            "recurrence_rule",
            "status",
            "start_at",
            "end_at",
            "next_run_at",
            "last_run_at",
            "last_success_at",
            "last_delivery_status",
            "last_sent_at",
            "consecutive_failure_count",
            "created_by",
            "created_by_summary",
            "recipient_count",
            "human_readable_recurrence",
            "allowed_actions",
            "latest_job",
            "latest_delivery",
            "next_runs_preview",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_recipient_count(self, obj):
        if hasattr(obj, "recipient_entries"):
            return obj.recipient_entries.filter(is_active=True).count()
        return 0

    def get_human_readable_recurrence(self, obj):
        frequency = obj.frequency
        delivery_time = obj.delivery_time.strftime("%I:%M %p") if obj.delivery_time else "--"
        return f"{frequency.title()} at {delivery_time}"

    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request is not None else None
        return get_schedule_allowed_actions(
            schedule=obj,
            user=user,
            organization=getattr(obj, "organization", None),
            data_center=getattr(obj, "data_center", None),
            request=request,
        )

    def get_latest_job(self, obj):
        job = getattr(obj, "last_job", None)
        if not job and hasattr(obj, "executions"):
            job = obj.executions.select_related("definition", "template", "requested_by").order_by("-queued_at").first()
        return ReportJobListSerializer(job, context=self.context).data if job else None

    def get_latest_delivery(self, obj):
        delivery = None
        if hasattr(obj, "executions"):
            delivery = obj.executions.order_by("-queued_at").prefetch_related("deliveries").first()
            if delivery and hasattr(delivery, "deliveries"):
                delivery = delivery.deliveries.order_by("-created_at").first()
        return _ReportDeliverySummarySerializer(delivery, context=self.context).data if delivery else None

    def get_next_runs_preview(self, obj):
        runs = calculate_next_runs(
            recurrence_rule=obj.recurrence_rule or {
                "frequency": obj.frequency,
                "delivery_time": obj.delivery_time.strftime("%H:%M:%S") if obj.delivery_time else "06:00:00",
            },
            after=obj.next_run_at or timezone.now(),
            start_at=obj.start_at,
            end_at=obj.end_at,
            count=5,
        )
        return [run.isoformat() for run in runs]


class ReportScheduleListSerializer(_ReportScheduleBaseSerializer):
    class Meta(_ReportScheduleBaseSerializer.Meta):
        fields = _ReportScheduleBaseSerializer.Meta.fields
        read_only_fields = fields


class ReportScheduleDetailSerializer(_ReportScheduleBaseSerializer):
    recipients = ReportScheduleRecipientSerializer(source="recipient_entries", many=True, read_only=True)

    class Meta(_ReportScheduleBaseSerializer.Meta):
        fields = _ReportScheduleBaseSerializer.Meta.fields + ("recipients",)
        read_only_fields = fields


class ReportScheduleWriteSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(write_only=True)
    data_center_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    definition_code = serializers.CharField(write_only=True)
    template_id = serializers.UUIDField(write_only=True)
    recipients = serializers.ListField(child=serializers.DictField(), required=False, default=list, write_only=True)
    attachment_formats = serializers.ListField(child=serializers.ChoiceField(choices=["PDF", "CSV", "XLSX"]), required=False, default=list)
    primary_format = serializers.ChoiceField(choices=["PDF", "CSV", "XLSX"], required=True)

    class Meta:
        model = ReportSchedule
        fields = (
            "organization_id",
            "data_center_id",
            "definition_code",
            "template_id",
            "name",
            "parameters",
            "primary_format",
            "attachment_formats",
            "recurrence_rule",
            "status",
            "start_at",
            "end_at",
            "recipients",
        )

    def _normalize_schedule_recipients(self, recipients):
        normalized = []
        seen = set()
        email_recipients = []
        sms_recipients = []
        for recipient in _recipient_values(recipients):
            key = (
                recipient["channel"],
                recipient["recipient_type"],
                recipient["destination"].lower() if recipient["channel"] == ReportRecipientChannel.EMAIL else recipient["destination"],
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(recipient)
            if recipient["channel"] == ReportRecipientChannel.EMAIL:
                email_recipients.append(recipient["destination"])
            else:
                sms_recipients.append(recipient["destination"])
        return normalized, email_recipients, sms_recipients

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization = Organization.objects.filter(pk=attrs["organization_id"]).first()
        if not organization:
            raise serializers.ValidationError({"organization_id": "Organization does not exist."})
        data_center = None
        if attrs.get("data_center_id"):
            data_center = DataCenter.objects.filter(pk=attrs["data_center_id"]).first()
            if not data_center:
                raise serializers.ValidationError({"data_center_id": "Data center does not exist."})
        validate_report_scope(user=user, organization=organization, data_center=data_center)

        definition = ReportDefinition.objects.filter(code=attrs["definition_code"]).first()
        if not definition:
            raise serializers.ValidationError({"definition_code": "Unsupported report definition."})
        if not definition.is_active:
            raise serializers.ValidationError({"definition_code": "Report definition is inactive."})
        if not definition.supports_scheduling:
            raise serializers.ValidationError({"definition_code": "Report definition does not support scheduling."})

        template = ReportTemplate.objects.select_related("definition", "organization").filter(pk=attrs["template_id"]).first()
        if not template:
            raise serializers.ValidationError({"template_id": "Template does not exist."})
        if not template.is_active:
            raise serializers.ValidationError({"template_id": "Template is inactive."})
        if template.definition_id and template.definition_id != definition.id:
            raise serializers.ValidationError({"template_id": "Template definition must match the selected report definition."})
        if template.organization_id not in {None, organization.id}:
            raise serializers.ValidationError({"template_id": "Template must belong to the selected organization or be global."})

        recurrence_rule = attrs.get("recurrence_rule") or {}
        if not isinstance(recurrence_rule, dict):
            raise serializers.ValidationError({"recurrence_rule": "Recurrence rule must be an object."})
        frequency = str(recurrence_rule.get("frequency") or "DAILY").upper()
        if frequency not in {"DAILY", "WEEKLY", "MONTHLY", "QUARTERLY"}:
            raise serializers.ValidationError({"recurrence_rule": "Unsupported recurrence frequency."})
        attrs["frequency"] = frequency
        delivery_time_value = recurrence_rule.get("delivery_time") or recurrence_rule.get("time") or "06:00:00"
        if isinstance(delivery_time_value, str):
            try:
                delivery_time = datetime.strptime(delivery_time_value.strip(), "%H:%M:%S").time()
            except ValueError:
                try:
                    delivery_time = datetime.strptime(delivery_time_value.strip(), "%H:%M").time()
                except ValueError:
                    raise serializers.ValidationError({"recurrence_rule": "Delivery time must be in HH:MM or HH:MM:SS format."})
        else:
            delivery_time = delivery_time_value
        attrs["delivery_time"] = delivery_time
        if attrs.get("start_at") and attrs.get("end_at") and attrs["start_at"] > attrs["end_at"]:
            raise serializers.ValidationError({"end_at": "End date must be after the start date."})
        if attrs.get("status") == ReportScheduleStatus.EXPIRED:
            raise serializers.ValidationError({"status": "Expired is managed by the backend."})

        normalized_parameters = normalize_report_parameters(attrs.get("parameters") or {})
        if normalized_parameters.get("report_type") and normalized_parameters["report_type"] != definition.code:
            raise serializers.ValidationError({"parameters": "report_type does not match the selected definition."})
        normalized_parameters["report_type"] = definition.code

        attachment_formats = list(dict.fromkeys([fmt.upper() for fmt in attrs.get("attachment_formats") or [] if fmt]))
        supported_formats = {str(fmt).upper() for fmt in definition.supported_formats or []}
        invalid_attachments = [fmt for fmt in attachment_formats if supported_formats and fmt not in supported_formats]
        if invalid_attachments:
            raise serializers.ValidationError({"attachment_formats": f"Unsupported attachment formats: {', '.join(invalid_attachments)}"})

        normalized_recipients, email_recipients, sms_recipients = self._normalize_schedule_recipients(attrs.get("recipients") or [])
        if not normalized_recipients:
            raise serializers.ValidationError({"recipients": "At least one email or SMS recipient is required."})

        attrs["organization"] = organization
        attrs["data_center"] = data_center
        attrs["definition"] = definition
        attrs["template"] = template
        attrs["parameters"] = normalized_parameters
        attrs["recurrence_rule"] = recurrence_rule
        attrs["attachment_formats"] = attachment_formats
        attrs["recipients"] = normalized_recipients
        attrs["_email_recipients"] = email_recipients
        attrs["_sms_recipients"] = sms_recipients
        return attrs

    def _apply_recipients(self, schedule, recipients):
        normalized, email_recipients, sms_recipients = self._normalize_schedule_recipients(recipients)
        schedule.recipients = email_recipients
        schedule.sms_recipients = sms_recipients
        schedule.send_sms = bool(sms_recipients)
        schedule._normalized_recipients = normalized

    def create(self, validated_data):
        recipients = validated_data.pop("recipients", [])
        validated_data.pop("_email_recipients", None)
        validated_data.pop("_sms_recipients", None)
        validated_data.pop("organization_id", None)
        validated_data.pop("data_center_id", None)
        validated_data.pop("definition_code", None)
        validated_data.pop("template_id", None)
        schedule = ReportSchedule(**validated_data)
        self._apply_recipients(schedule, recipients)
        schedule.attach_raw_data = "CSV" in schedule.attachment_formats
        schedule.output_format = "PDF_CSV" if schedule.primary_format == "PDF" and "CSV" in schedule.attachment_formats else schedule.primary_format
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            schedule.created_by = request.user
        schedule.save()
        self._sync_schedule_recipient_rows(schedule)
        return schedule

    def update(self, instance, validated_data):
        recipients = validated_data.pop("recipients", None)
        validated_data.pop("_email_recipients", None)
        validated_data.pop("_sms_recipients", None)
        validated_data.pop("organization_id", None)
        validated_data.pop("data_center_id", None)
        validated_data.pop("definition_code", None)
        validated_data.pop("template_id", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if recipients is not None:
            self._apply_recipients(instance, recipients)
            self._sync_schedule_recipient_rows(instance)
        instance.attach_raw_data = "CSV" in instance.attachment_formats
        instance.output_format = "PDF_CSV" if instance.primary_format == "PDF" and "CSV" in instance.attachment_formats else instance.primary_format
        instance.save()
        return instance

    def _sync_schedule_recipient_rows(self, schedule: ReportSchedule):
        normalized = getattr(schedule, "_normalized_recipients", None)
        if normalized is None:
            normalized, _, _ = self._normalize_schedule_recipients(schedule.recipients if isinstance(schedule.recipients, list) else [])
        with transaction.atomic():
            schedule.recipient_entries.all().delete()
            for recipient in normalized:
                ReportScheduleRecipient.objects.create(
                    schedule=schedule,
                    channel=recipient["channel"],
                    recipient_type=recipient["recipient_type"],
                    destination=recipient["destination"],
                    display_name=recipient.get("display_name"),
                    is_active=True,
                )


class ReportArtifactSerializer(serializers.ModelSerializer):
    job = _ReportJobSummarySerializer(read_only=True)
    download_endpoint = serializers.SerializerMethodField()
    is_downloadable = serializers.SerializerMethodField()
    artifact_status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ReportArtifact
        fields = (
            "id",
            "job",
            "artifact_type",
            "format",
            "file_name",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "status",
            "artifact_status_display",
            "expires_at",
            "created_at",
            "updated_at",
            "download_endpoint",
            "is_downloadable",
        )
        read_only_fields = fields

    def get_download_endpoint(self, obj):
        request = self.context.get("request")
        url = reverse("report-artifact-api-download", kwargs={"pk": obj.pk})
        return request.build_absolute_uri(url) if request else url

    def get_is_downloadable(self, obj):
        if obj.status != ReportArtifactStatus.AVAILABLE:
            return False
        if obj.expires_at and obj.expires_at <= timezone.now():
            return False
        return bool(obj.file)


class ReportDeliverySerializer(serializers.ModelSerializer):
    job = _ReportJobSummarySerializer(read_only=True)
    schedule = ReportScheduleListSerializer(read_only=True)
    artifact = _ReportArtifactSummarySerializer(read_only=True)
    masked_destination = serializers.SerializerMethodField()
    retry_available = serializers.SerializerMethodField()
    safe_error_code = serializers.CharField(source="error_code", read_only=True)
    safe_error_message = serializers.CharField(source="error_message", read_only=True)

    class Meta:
        model = ReportDelivery
        fields = (
            "id",
            "job",
            "schedule",
            "artifact",
            "channel",
            "recipient_type",
            "destination_snapshot",
            "masked_destination",
            "attempt_number",
            "status",
            "provider_message_id",
            "queued_at",
            "attempted_at",
            "accepted_at",
            "delivered_at",
            "failed_at",
            "safe_error_code",
            "safe_error_message",
            "retry_available",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_masked_destination(self, obj):
        return _mask_destination(obj.channel, obj.destination_snapshot)

    def get_retry_available(self, obj):
        return obj.status == ReportDeliveryStatus.FAILED


class ReportDefinitionParameterSchemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportDefinition
        fields = ("code", "name", "parameter_schema", "supported_formats", "supports_scheduling", "supports_raw_attachment", "supports_charts")
        read_only_fields = fields


class ReportJobCreateResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    reference = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    trigger_source = serializers.CharField(read_only=True)
    queued_at = serializers.DateTimeField(read_only=True)
    detail_url = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
