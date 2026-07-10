from django.contrib import admin

from .models import ReportJob, ReportTemplate


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "is_active", "created_at", "updated_at")
    list_filter = ("organization", "is_active", "created_at")
    search_fields = ("name", "code", "description", "organization__name", "organization__code")
    ordering = ("-created_at",)


@admin.register(ReportJob)
class ReportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "template", "organization", "data_center", "requested_by", "status", "started_at", "completed_at")
    list_filter = ("status", "organization", "data_center", "template", "created_at", "completed_at")
    search_fields = (
        "template__name",
        "template__code",
        "requested_by__username",
        "requested_by__email",
        "organization__name",
        "organization__code",
        "error_message",
    )
    readonly_fields = ("status", "file", "started_at", "completed_at", "error_message", "created_at", "updated_at")
    ordering = ("-created_at",)
