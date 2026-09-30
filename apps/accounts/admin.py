from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "email", "phone", "is_active", "is_staff", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser", "is_mfa_enabled", "date_joined")
    search_fields = ("username", "full_name", "email", "phone")
    ordering = ("username",)
