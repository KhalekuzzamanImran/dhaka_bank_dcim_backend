from django.contrib import admin
from .models import DeviceType, Vendor, DeviceModel, Device, DeviceProtocolConfig, DeviceCredential, PollingProfile, DevicePollingConfig, SNMPOIDMapping, ModbusRegisterMapping

admin.site.register(DeviceType)
admin.site.register(Vendor)
admin.site.register(DeviceModel)
admin.site.register(Device)
admin.site.register(DeviceProtocolConfig)
admin.site.register(DeviceCredential)
admin.site.register(PollingProfile)
admin.site.register(DevicePollingConfig)
admin.site.register(SNMPOIDMapping)
admin.site.register(ModbusRegisterMapping)
