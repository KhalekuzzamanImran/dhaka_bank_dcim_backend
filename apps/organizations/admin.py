from django.contrib import admin
from .models import Organization

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "status", "email", "phone", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "code", "email", "phone")
    ordering = ("name",)
