import re

from django.contrib import admin
from django.contrib import messages

from .models import SNMPMIBDefinition, SNMPTrapEvent, SNMPTrapOIDMapping, SNMPTrapSource


@admin.register(SNMPTrapSource)
class SNMPTrapSourceAdmin(admin.ModelAdmin):
    ordering = ("-created_at", "-updated_at")


@admin.register(SNMPTrapOIDMapping)
class SNMPTrapOIDMappingAdmin(admin.ModelAdmin):
    list_display = ("trap_oid", "event_code", "event_name", "severity", "device_type", "vendor", "device_model", "create_alert", "is_active", "created_at")
    list_filter = ("severity", "create_alert", "is_active", "device_type", "vendor")
    search_fields = ("trap_oid", "event_code", "event_name", "message_template")
    ordering = ("-created_at", "-updated_at")


@admin.register(SNMPMIBDefinition)
class SNMPMIBDefinitionAdmin(admin.ModelAdmin):
    list_display = ("module_name", "symbol", "oid", "status", "is_active", "imported_at", "source_file")
    list_filter = ("module_name", "status", "is_active")
    search_fields = ("module_name", "symbol", "oid", "description")
    ordering = ("module_name", "symbol", "oid")


@admin.register(SNMPTrapEvent)
class SNMPTrapEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_ip",
        "trap_oid",
        "resolution_source",
        "event_code",
        "severity",
        "is_mapped",
        "requires_mapping_review",
        "is_processed",
        "received_at",
    )
    list_filter = (
        "resolution_source",
        "severity",
        "is_mapped",
        "requires_mapping_review",
        "is_processed",
        "received_at",
        "mib_module",
        "mib_status",
    )
    search_fields = ("source_ip", "trap_oid", "event_code", "event_name", "message", "mib_module", "mib_symbol")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-received_at", "-created_at", "-updated_at")

    actions = ["create_trap_mapping_from_event"]

    @admin.action(description="Create Trap Mapping from Event")
    def create_trap_mapping_from_event(self, request, queryset):
        created = 0
        skipped = 0
        for event in queryset.select_related("device", "device__device_type", "device__device_model", "device__device_model__vendor"):
            device = event.device
            if not device or not device.device_type:
                skipped += 1
                continue
            symbol_source = event.mib_symbol or event.event_name or event.trap_oid
            event_code = _suggest_event_code(symbol_source)
            event_name = event.mib_symbol or event.event_name or "Unmapped SNMP Trap"
            defaults = {
                "event_name": event_name,
                "severity": event.severity or "WARNING",
                "message_template": event.mib_description or event.message or "Review trap mapping created from event",
                "create_alert": False,
                "is_active": False,
            }
            mapping, was_created = SNMPTrapOIDMapping.objects.get_or_create(
                device_type=device.device_type,
                vendor=device.device_model.vendor if device.device_model and device.device_model.vendor_id else None,
                device_model=device.device_model,
                trap_oid=event.trap_oid,
                event_code=event_code,
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                skipped += 1
        if created:
            self.message_user(request, f"Created {created} draft trap mapping(s).", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} event(s) because a draft mapping already existed or the device scope was incomplete.", level=messages.WARNING)


def _suggest_event_code(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "UNMAPPED_SNMP_TRAP"
    text = text.replace("::", "_").replace("-", "_").replace(".", "_")
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.upper() or "UNMAPPED_SNMP_TRAP"
