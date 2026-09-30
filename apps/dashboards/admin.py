from django.contrib import admin
from .models import Dashboard, DashboardWidget

@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "data_center", "is_default", "is_active", "updated_at")
    list_filter = ("organization", "data_center", "is_default", "is_active")
    search_fields = ("name", "code", "organization__name", "data_center__name")
    ordering = ("organization__name", "name")


@admin.register(DashboardWidget)
class DashboardWidgetAdmin(admin.ModelAdmin):
    list_display = ("title", "dashboard", "widget_type", "data_center", "device", "metric", "is_active")
    list_filter = ("widget_type", "is_active", "dashboard", "data_center")
    search_fields = ("title", "widget_type", "dashboard__name", "device__name", "metric__code")
    ordering = ("dashboard__name", "position_y", "position_x")
