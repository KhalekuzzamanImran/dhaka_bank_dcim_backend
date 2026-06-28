from django.contrib import admin
from .models import AlertRule, AlertEvent

admin.site.register(AlertRule)
admin.site.register(AlertEvent)
