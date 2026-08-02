from django.contrib import admin

from .models import (
    ReportArtifact,
    ReportDelivery,
    ReportDefinition,
    ReportJob,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportTemplate,
)


@admin.register(ReportDefinition)
class ReportDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "version", "is_active", "created_at", "updated_at")
    list_filter = ("category", "is_active", "version", "created_at")
    search_fields = ("code", "name", "description", "handler_key")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("code",)


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "definition", "organization", "version", "is_default", "is_active", "created_at", "updated_at")
    list_filter = ("organization", "definition", "is_default", "is_active", "created_at")
    search_fields = ("name", "code", "description", "organization__name", "organization__code", "definition__code", "definition__name")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportSchedule)
class ReportScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "organization",
        "data_center",
        "definition",
        "template",
        "status",
        "frequency",
        "delivery_time",
        "is_active",
        "next_run_at",
        "last_success_at",
    )
    list_filter = (
        "organization",
        "data_center",
        "definition",
        "template",
        "status",
        "frequency",
        "output_format",
        "is_active",
        "last_delivery_status",
        "created_at",
    )
    search_fields = (
        "name",
        "report_type",
        "organization__name",
        "organization__code",
        "definition__code",
        "definition__name",
        "template__name",
        "template__code",
        "last_error_message",
    )
    readonly_fields = (
        "next_run_at",
        "last_run_at",
        "last_success_at",
        "last_sent_at",
        "consecutive_failure_count",
        "last_delivery_status",
        "last_error_message",
        "last_job",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)


@admin.register(ReportScheduleRecipient)
class ReportScheduleRecipientAdmin(admin.ModelAdmin):
    list_display = ("schedule", "channel", "recipient_type", "destination", "display_name", "user", "is_active", "created_at")
    list_filter = ("channel", "recipient_type", "is_active", "created_at")
    search_fields = ("destination", "display_name", "schedule__name", "schedule__organization__name", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportArtifact)
class ReportArtifactAdmin(admin.ModelAdmin):
    list_display = ("job", "artifact_type", "format", "file_name", "status", "size_bytes", "expires_at", "created_at")
    list_filter = ("artifact_type", "format", "status", "created_at")
    search_fields = ("file_name", "checksum_sha256", "job__id", "job__organization__name", "job__organization__code")
    readonly_fields = ("checksum_sha256", "size_bytes", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportDelivery)
class ReportDeliveryAdmin(admin.ModelAdmin):
    list_display = ("job", "schedule", "channel", "recipient", "recipient_type", "attempt_number", "status", "destination_snapshot", "queued_at")
    list_filter = ("channel", "recipient_type", "status", "created_at", "queued_at")
    search_fields = ("destination_snapshot", "provider_message_id", "error_code", "error_message", "job__id", "schedule__name")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportJob)
class ReportJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "definition",
        "template",
        "schedule",
        "organization",
        "data_center",
        "requested_by",
        "trigger_source",
        "status",
        "started_at",
        "completed_at",
    )
    list_filter = (
        "status",
        "trigger_source",
        "organization",
        "data_center",
        "definition",
        "template",
        "schedule",
        "created_at",
        "completed_at",
    )
    search_fields = (
        "definition__code",
        "definition__name",
        "template__name",
        "template__code",
        "requested_by__username",
        "requested_by__email",
        "organization__name",
        "organization__code",
        "error_message",
    )
    readonly_fields = (
        "status",
        "trigger_source",
        "queued_at",
        "started_at",
        "completed_at",
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
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
