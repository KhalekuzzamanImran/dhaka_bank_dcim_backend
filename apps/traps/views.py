from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import SNMPTrapSource, SNMPTrapOIDMapping, SNMPTrapEvent
from .serializers import SNMPTrapSourceSerializer, SNMPTrapOIDMappingSerializer, SNMPTrapEventSerializer


class SNMPTrapSourceViewSet(ScopedModelViewSet):
    queryset = SNMPTrapSource.objects.select_related("organization", "data_center", "device").all()
    serializer_class = SNMPTrapSourceSerializer
    permission_module = "traps"
    audit_resource_type = "SNMPTrapSource"
    filterset_fields = ["organization", "data_center", "source_ip", "is_enabled"]
    search_fields = ["source_ip", "description"]


class SNMPTrapOIDMappingViewSet(AuditModelViewSet):
    queryset = SNMPTrapOIDMapping.objects.select_related("device_type", "vendor", "device_model").all()
    serializer_class = SNMPTrapOIDMappingSerializer
    permission_module = "traps"
    audit_resource_type = "SNMPTrapOIDMapping"
    filterset_fields = ["device_type", "vendor", "device_model", "trap_oid", "severity", "create_alert", "is_active"]
    search_fields = ["trap_oid", "event_code", "event_name"]


class SNMPTrapEventViewSet(ScopedModelViewSet):
    queryset = SNMPTrapEvent.objects.select_related("organization", "data_center", "device").all()
    serializer_class = SNMPTrapEventSerializer
    permission_module = "traps"
    audit_resource_type = "SNMPTrapEvent"
    http_method_names = ["get", "head", "options"]
    filterset_fields = ["organization", "data_center", "device", "source_ip", "trap_oid", "severity", "is_mapped", "is_processed"]
    search_fields = ["trap_oid", "event_code", "event_name", "message"]
