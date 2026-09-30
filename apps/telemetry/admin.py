from django.contrib import admin

from .models import DeviceEvent, LatestTelemetry, MetricDefinition, TelemetryIngestLog, TelemetryPoint


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


@admin.register(TelemetryPoint)
class TelemetryPointAdmin(admin.ModelAdmin):
    list_display = ("recorded_at", "device", "metric", "display_value", "quality", "source", "organization", "data_center")
    list_filter = ("quality", "source", "organization", "data_center", "metric")
    search_fields = ("device__name", "device__code", "metric__code", "metric__name", "raw_value_text", "value_text", "source")
    list_select_related = ("device", "metric", "organization", "data_center")
    date_hierarchy = "time"
    ordering = ("-time",)

    @admin.display(description="Recorded at", ordering="time")
    def recorded_at(self, obj):
        return obj.time

    @admin.display(description="Value")
    def display_value(self, obj):
        for field in ("value_float", "value_integer", "value_boolean", "value_text", "raw_value_text"):
            value = getattr(obj, field, None)
            if value is not None:
                return value
        return "—"


@admin.register(TelemetryIngestLog)
class TelemetryIngestLogAdmin(admin.ModelAdmin):
    list_display = ("started_at", "device", "protocol", "status", "duration_ms", "finished_at", "ingest_id")
    list_filter = ("protocol", "status", "started_at")
    search_fields = ("device__name", "device__code", "protocol", "status", "ingest_id", "error_message")
    list_select_related = ("device",)
    date_hierarchy = "started_at"
    ordering = ("-started_at",)


@admin.register(DeviceEvent)
class DeviceEventAdmin(admin.ModelAdmin):
    list_display = ("event_name", "event_code", "device", "organization", "data_center", "severity", "occurred_at")
    list_filter = ("severity", "organization", "data_center", "occurred_at")
    search_fields = ("event_name", "event_code", "message", "device__name", "device__code")
    list_select_related = ("device", "organization", "data_center")
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
