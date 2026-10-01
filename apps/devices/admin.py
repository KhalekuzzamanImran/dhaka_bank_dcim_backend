from django.contrib import admin
from django import forms

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
from collectors.snmp_collector.security import encrypt_secret


class DeviceCredentialAdminForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        label="Password",
        help_text="Leave blank to keep the current password.",
        widget=forms.PasswordInput(render_value=False),
    )
    snmp_community = forms.CharField(
        required=False,
        label="SNMP community",
        help_text="Leave blank to keep the current community string.",
        widget=forms.PasswordInput(render_value=False),
    )
    snmp_v3_auth_key = forms.CharField(
        required=False,
        label="SNMP v3 auth key",
        help_text="Leave blank to keep the current auth key.",
        widget=forms.PasswordInput(render_value=False),
    )
    snmp_v3_priv_key = forms.CharField(
        required=False,
        label="SNMP v3 privacy key",
        help_text="Leave blank to keep the current privacy key.",
        widget=forms.PasswordInput(render_value=False),
    )

    class Meta:
        model = DeviceCredential
        fields = "__all__"
        exclude = (
            "password_encrypted",
            "snmp_community_encrypted",
            "snmp_v3_auth_key_encrypted",
            "snmp_v3_priv_key_encrypted",
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        secret_fields = {
            "password": "password_encrypted",
            "snmp_community": "snmp_community_encrypted",
            "snmp_v3_auth_key": "snmp_v3_auth_key_encrypted",
            "snmp_v3_priv_key": "snmp_v3_priv_key_encrypted",
        }
        for form_field, model_field in secret_fields.items():
            value = self.cleaned_data.get(form_field)
            if value:
                setattr(instance, model_field, encrypt_secret(value))
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
    form = DeviceCredentialAdminForm
    list_display = ("device", "protocol", "snmp_version", "username", "is_active", "updated_at")
    list_filter = ("protocol", "snmp_version", "is_active")
    search_fields = ("device__name", "device__code", "username")
    list_select_related = ("device",)
    readonly_fields = ("secret_status",)

    @admin.display(description="Stored credentials")
    def secret_status(self, obj):
        return ", ".join(
            f"{label}: {'configured' if getattr(obj, field_name) else 'not configured'}"
            for label, field_name in (
                ("Password", "password_encrypted"),
                ("SNMP community", "snmp_community_encrypted"),
                ("SNMP v3 auth key", "snmp_v3_auth_key_encrypted"),
                ("SNMP v3 privacy key", "snmp_v3_priv_key_encrypted"),
            )
        )


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
