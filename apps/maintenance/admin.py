from django.contrib import admin
from .models import MaintenanceTicket

@admin.register(MaintenanceTicket)
class MaintenanceTicketAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "data_center", "device", "maintenance_type", "status", "scheduled_at", "assigned_to")
    list_filter = ("organization", "data_center", "maintenance_type", "status", "scheduled_at")
    search_fields = ("title", "description", "organization__name", "data_center__name", "device__name", "assigned_to__username")
    ordering = ("-scheduled_at", "-created_at")
