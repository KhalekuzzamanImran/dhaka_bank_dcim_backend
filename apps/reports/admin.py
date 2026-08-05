from django.contrib import admin

from .models import ReportArtifact, ReportDelivery, ReportDefinition, ReportJob, ReportSchedule, ReportScheduleRecipient, ReportScheduleRun, ReportTemplate


@admin.register(ReportDefinition)
class ReportDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "generator_key", "is_active", "version", "created_at")
    list_filter = ("category", "is_active", "created_at")
    search_fields = ("code", "name", "description", "generator_key")
    ordering = ("category", "name")


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "definition", "organization", "primary_format", "is_active", "created_at", "updated_at")
    list_filter = ("organization", "definition", "primary_format", "is_active", "created_at")
    search_fields = ("name", "code", "description", "organization__name", "organization__code", "definition__code")
    ordering = ("-created_at",)


@admin.register(ReportJob)
class ReportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "definition", "template", "organization", "data_center", "requested_by", "status", "started_at", "completed_at")
    list_filter = ("status", "organization", "data_center", "template", "definition", "created_at", "completed_at")
    search_fields = ("template__name", "template__code", "definition__code", "requested_by__username", "requested_by__email", "organization__name", "organization__code", "error_message")
    readonly_fields = ("status", "started_at", "completed_at", "error_message", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportSchedule)
class ReportScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "data_center", "template", "status", "frequency", "delivery_time", "next_run_at", "last_sent_at")
    list_filter = ("organization", "data_center", "template", "status", "frequency", "primary_format", "last_delivery_status", "created_at")
    search_fields = ("name", "template__name", "template__code", "template__definition__code", "organization__name", "organization__code", "last_error_message")
    readonly_fields = ("next_run_at", "last_run_at", "last_sent_at", "last_delivery_status", "last_error_message", "last_job", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportScheduleRun)
class ReportScheduleRunAdmin(admin.ModelAdmin):
    list_display = ("schedule", "organization", "status", "trigger_source", "scheduled_for", "window_start", "window_end", "job", "created_at")
    list_filter = ("organization", "schedule", "status", "trigger_source", "created_at")
    search_fields = ("schedule__name", "schedule__template__definition__code", "error_message")
    readonly_fields = ("queued_at", "started_at", "completed_at", "job", "error_message", "trigger_source", "snapshot", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportScheduleRecipient)
class ReportScheduleRecipientAdmin(admin.ModelAdmin):
    list_display = ("schedule", "channel", "display_name", "email_address", "phone_number", "is_active", "created_at")
    list_filter = ("channel", "is_active", "created_at")
    search_fields = ("schedule__name", "display_name", "email_address", "phone_number")
    ordering = ("-created_at",)


@admin.register(ReportArtifact)
class ReportArtifactAdmin(admin.ModelAdmin):
    list_display = ("job", "format", "size_bytes", "retention_expires_at", "created_at")
    list_filter = ("format", "created_at", "retention_expires_at")
    search_fields = ("job__template__name", "job__template__code", "original_filename", "checksum_sha256")
    readonly_fields = ("job", "format", "file", "original_filename", "content_type", "size_bytes", "checksum_sha256", "retention_expires_at", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReportDelivery)
class ReportDeliveryAdmin(admin.ModelAdmin):
    list_display = ("job", "channel", "recipient", "status", "retry_count", "created_at")
    list_filter = ("channel", "status", "created_at")
    search_fields = ("job__template__name", "job__template__code", "recipient", "provider_message_id", "error_message")
    readonly_fields = ("job", "schedule_recipient", "channel", "recipient", "status", "queued_at", "started_at", "sent_at", "failed_at", "retry_count", "provider_message_id", "provider_response", "error_code", "error_message", "created_at", "updated_at")
    ordering = ("-created_at",)
