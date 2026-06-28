from rest_framework.routers import DefaultRouter
from .views import DeviceTypeViewSet, VendorViewSet, DeviceModelViewSet, DeviceViewSet, DeviceProtocolConfigViewSet, DeviceCredentialViewSet, PollingProfileViewSet, DevicePollingConfigViewSet, SNMPOIDMappingViewSet, ModbusRegisterMappingViewSet

router = DefaultRouter()
router.register(r"device-types", DeviceTypeViewSet, basename="device-type")
router.register(r"vendors", VendorViewSet, basename="vendor")
router.register(r"device-models", DeviceModelViewSet, basename="device-model")
router.register(r"devices", DeviceViewSet, basename="device")
router.register(r"device-protocol-configs", DeviceProtocolConfigViewSet, basename="device-protocol-config")
router.register(r"device-credentials", DeviceCredentialViewSet, basename="device-credential")
router.register(r"polling-profiles", PollingProfileViewSet, basename="polling-profile")
router.register(r"device-polling-configs", DevicePollingConfigViewSet, basename="device-polling-config")
router.register(r"snmpoidmappings", SNMPOIDMappingViewSet, basename="snmpoidmapping")
router.register(r"modbus-register-mappings", ModbusRegisterMappingViewSet, basename="modbus-register-mapping")

urlpatterns = router.urls
