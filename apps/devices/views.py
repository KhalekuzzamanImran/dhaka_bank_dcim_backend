from django.db.models import Count
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.access import get_accessible_devices_for_user
from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import DeviceType, Vendor, DeviceModel, Device, DeviceProtocolConfig, DeviceCredential, PollingProfile, DevicePollingConfig, SNMPOIDMapping, ModbusRegisterMapping
from .serializers import DeviceTypeSerializer, VendorSerializer, DeviceModelSerializer, DeviceSerializer, DeviceProtocolConfigSerializer, DeviceCredentialSerializer, PollingProfileSerializer, DevicePollingConfigSerializer, SNMPOIDMappingSerializer, ModbusRegisterMappingSerializer


class DeviceTypeViewSet(AuditModelViewSet):
    queryset = DeviceType.objects.all()
    serializer_class = DeviceTypeSerializer
    permission_module = "device"
    audit_resource_type = "DeviceType"
    search_fields = ["name", "code", "category"]

    def get_permissions(self):
        if self.action == "available_for_me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def available_for_me(self, request):
        devices = get_accessible_devices_for_user(request.user)
        if not devices.exists():
            return Response([])
        rows = (
            devices.values("device_type_id", "device_type__name", "device_type__code")
            .annotate(device_count=Count("id"))
            .order_by("device_type__name")
        )
        return Response(
            [
                {
                    "id": row["device_type_id"],
                    "name": row["device_type__name"],
                    "code": row["device_type__code"],
                    "device_count": row["device_count"],
                }
                for row in rows
            ]
        )


class VendorViewSet(AuditModelViewSet):
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    permission_module = "device"
    audit_resource_type = "Vendor"
    search_fields = ["name", "code"]


class DeviceModelViewSet(AuditModelViewSet):
    queryset = DeviceModel.objects.select_related("vendor", "device_type").all()
    serializer_class = DeviceModelSerializer
    permission_module = "device"
    audit_resource_type = "DeviceModel"
    search_fields = ["name", "model_number"]


class DeviceViewSet(ScopedModelViewSet):
    access_scope = "device"
    device_field = "id"
    queryset = Device.objects.select_related(
        "organization", "data_center", "room", "rack", "device_type", "device_model",
        "polling_config", "polling_config__polling_profile",
    ).all()
    serializer_class = DeviceSerializer
    permission_module = "device"
    audit_resource_type = "Device"
    filterset_fields = ["organization", "data_center", "room", "rack", "device_type", "status", "is_active"]
    search_fields = ["name", "code", "hostname", "ip_address", "serial_number", "asset_tag"]
    ordering_fields = ["name", "code", "status", "last_seen_at", "created_at"]


class DeviceProtocolConfigViewSet(ScopedModelViewSet):
    access_scope = "device"
    queryset = DeviceProtocolConfig.objects.select_related("device", "device__data_center").all()
    serializer_class = DeviceProtocolConfigSerializer
    permission_module = "device"
    audit_resource_type = "DeviceProtocolConfig"
    filterset_fields = ["device", "protocol", "is_enabled", "is_primary"]


class DeviceCredentialViewSet(ScopedModelViewSet):
    access_scope = "device"
    queryset = DeviceCredential.objects.select_related("device", "device__data_center").all()
    serializer_class = DeviceCredentialSerializer
    permission_module = "device.credential"
    audit_resource_type = "DeviceCredential"
    filterset_fields = ["device", "protocol", "is_active"]


class PollingProfileViewSet(AuditModelViewSet):
    queryset = PollingProfile.objects.all()
    serializer_class = PollingProfileSerializer
    permission_module = "device"
    audit_resource_type = "PollingProfile"
    filterset_fields = ["protocol", "is_active"]


class DevicePollingConfigViewSet(ScopedModelViewSet):
    access_scope = "device"
    queryset = DevicePollingConfig.objects.select_related("device", "device__data_center", "polling_profile").all()
    serializer_class = DevicePollingConfigSerializer
    permission_module = "device"
    audit_resource_type = "DevicePollingConfig"
    filterset_fields = ["device", "polling_profile", "is_enabled"]


class SNMPOIDMappingViewSet(AuditModelViewSet):
    queryset = SNMPOIDMapping.objects.select_related("device_type", "vendor", "device_model", "metric").all()
    serializer_class = SNMPOIDMappingSerializer
    permission_module = "device"
    audit_resource_type = "SNMPOIDMapping"
    filterset_fields = ["device_type", "vendor", "device_model", "metric", "is_active"]


class ModbusRegisterMappingViewSet(AuditModelViewSet):
    queryset = ModbusRegisterMapping.objects.select_related("device_type", "vendor", "device_model", "metric").all()
    serializer_class = ModbusRegisterMappingSerializer
    permission_module = "device"
    audit_resource_type = "ModbusRegisterMapping"
    filterset_fields = ["device_type", "vendor", "device_model", "metric", "is_active"]
