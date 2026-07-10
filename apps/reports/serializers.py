from __future__ import annotations

from rest_framework import serializers

from apps.common.access import get_access_scope
from apps.organizations.models import Organization

from .models import ReportJob, ReportJobStatus, ReportTemplate


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
