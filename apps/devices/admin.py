from django.contrib import admin

from .models import (
    Device,
    DeviceCredential,
    DeviceModel,
    DevicePollingConfig,
    DeviceProtocolConfig,
    DeviceType,
    ModbusRegisterMapping,
    PollingProfile,
    SNMPOIDMapping,
    Vendor,
)


@admin.register(DeviceType)
class DeviceTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "created_at")
    list_filter = ("category",)
    search_fields = ("name", "code", "description")
    ordering = ("name",)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "website", "created_at")
    search_fields = ("name", "code", "website")
    ordering = ("name",)


@admin.register(DeviceModel)
class DeviceModelAdmin(admin.ModelAdmin):
    list_display = ("name", "model_number", "vendor", "device_type", "created_at")
    list_filter = ("vendor", "device_type")
    search_fields = ("name", "model_number", "vendor__name", "device_type__name")
    ordering = ("vendor__name", "model_number")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "device_type", "device_model", "organization", "data_center", "room", "rack", "status", "is_active", "last_seen_at")
    list_filter = ("organization", "data_center", "device_type", "status", "is_active")
    search_fields = ("name", "code", "hostname", "ip_address", "serial_number", "asset_tag", "organization__name", "data_center__name", "device_type__name", "device_model__model_number")
    list_select_related = ("organization", "data_center", "room", "rack", "device_type", "device_model")
    date_hierarchy = "last_seen_at"
    ordering = ("organization__name", "data_center__name", "name")


@admin.register(DeviceProtocolConfig)
class DeviceProtocolConfigAdmin(admin.ModelAdmin):
    list_display = ("device", "protocol", "host", "port", "is_primary", "is_enabled", "timeout_seconds", "retry_count")
    list_filter = ("protocol", "is_primary", "is_enabled")
    search_fields = ("device__name", "device__code", "host")
    list_select_related = ("device",)
    ordering = ("device__name", "protocol")


@admin.register(DeviceCredential)
class DeviceCredentialAdmin(admin.ModelAdmin):
    list_display = ("device", "protocol", "snmp_version", "username", "is_active", "updated_at")
    list_filter = ("protocol", "snmp_version", "is_active")
    search_fields = ("device__name", "device__code", "username")
    list_select_related = ("device",)
    readonly_fields = ("password_encrypted", "snmp_community_encrypted", "snmp_v3_auth_key_encrypted", "snmp_v3_priv_key_encrypted")


@admin.register(PollingProfile)
class PollingProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "protocol", "priority", "interval_seconds", "timeout_seconds", "retry_count", "stale_after_seconds", "is_active")
    list_filter = ("protocol", "priority", "is_active")
    search_fields = ("name",)
    ordering = ("priority", "name")


@admin.register(DevicePollingConfig)
class DevicePollingConfigAdmin(admin.ModelAdmin):
    list_display = ("device", "polling_profile", "is_enabled", "last_polled_at", "next_poll_at", "consecutive_failures", "last_error_message")
    list_filter = ("is_enabled", "polling_profile__protocol", "polling_profile__priority")
    search_fields = ("device__name", "device__code", "polling_profile__name", "last_error_message")
    list_select_related = ("device", "polling_profile")
    ordering = ("next_poll_at", "device__name")


@admin.register(SNMPOIDMapping)
class SNMPOIDMappingAdmin(admin.ModelAdmin):
    list_display = ("metric", "oid", "device_type", "vendor", "device_model", "data_type", "scale_factor", "is_active")
    list_filter = ("device_type", "vendor", "device_model", "data_type", "is_active")
    search_fields = ("oid", "metric__code", "metric__name", "device_type__name", "vendor__name", "device_model__model_number")
    list_select_related = ("metric", "device_type", "vendor", "device_model")
    ordering = ("device_type__name", "metric__code", "oid")


@admin.register(ModbusRegisterMapping)
class ModbusRegisterMappingAdmin(admin.ModelAdmin):
    list_display = ("metric", "register_address", "device_type", "vendor", "device_model", "function_code", "data_type", "unit", "is_active")
    list_filter = ("device_type", "vendor", "device_model", "function_code", "data_type", "is_active")
    search_fields = ("metric__code", "metric__name", "device_type__name", "vendor__name", "device_model__model_number")
    list_select_related = ("metric", "device_type", "vendor", "device_model")
    ordering = ("device_type__name", "metric__code", "register_address")
