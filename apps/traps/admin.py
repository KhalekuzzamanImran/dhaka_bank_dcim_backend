from django.contrib import admin

from .models import SNMPTrapEvent, SNMPTrapOIDMapping, SNMPTrapSource


@admin.register(SNMPTrapSource)
class SNMPTrapSourceAdmin(admin.ModelAdmin):
    ordering = ("-created_at", "-updated_at")


@admin.register(SNMPTrapOIDMapping)
class SNMPTrapOIDMappingAdmin(admin.ModelAdmin):
    ordering = ("-created_at", "-updated_at")


@admin.register(SNMPTrapEvent)
class SNMPTrapEventAdmin(admin.ModelAdmin):
    list_display = ("id", "source_ip", "trap_oid", "event_code", "severity", "is_mapped", "is_processed", "received_at")
    list_filter = ("severity", "is_mapped", "is_processed", "received_at")
    search_fields = ("source_ip", "trap_oid", "event_code", "event_name", "message")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-received_at", "-created_at", "-updated_at")
