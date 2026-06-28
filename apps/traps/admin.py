from django.contrib import admin
from .models import SNMPTrapSource, SNMPTrapOIDMapping, SNMPTrapEvent

admin.site.register(SNMPTrapSource)
admin.site.register(SNMPTrapOIDMapping)
admin.site.register(SNMPTrapEvent)
