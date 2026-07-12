from django.contrib import admin

from .models import MetricDefinition, TelemetryPoint, LatestTelemetry, TelemetryIngestLog, DeviceEvent


@admin.register(MetricDefinition)
class MetricDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "data_type", "unit", "is_active", "created_at")
    list_filter = ("category", "data_type", "is_active", "created_at")
    search_fields = ("code", "name", "unit", "description")
    ordering = ("code",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(LatestTelemetry)
class LatestTelemetryAdmin(admin.ModelAdmin):
    list_display = ("device", "metric", "organization", "data_center", "quality", "last_seen_at", "created_at")
    list_filter = ("organization", "data_center", "metric", "quality", "last_seen_at")
    search_fields = (
        "device__name",
        "device__code",
        "metric__code",
        "metric__name",
        "source",
        "raw_value_text",
        "value_text",
    )
    ordering = ("-last_seen_at",)
    date_hierarchy = "last_seen_at"
    readonly_fields = (
        "organization",
        "data_center",
        "device",
        "metric",
        "value_float",
        "value_integer",
        "value_boolean",
        "value_text",
        "raw_value_text",
        "quality",
        "last_seen_at",
        "source",
        "created_at",
        "updated_at",
    )


admin.site.register(TelemetryPoint)
admin.site.register(TelemetryIngestLog)
admin.site.register(DeviceEvent)
