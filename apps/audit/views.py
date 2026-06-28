from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import AuditLog
from .serializers import AuditLogSerializer
class AuditLogViewSet(AuditModelViewSet):
    http_method_names = ['get','head','options']
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_module = 'audit'
    audit_resource_type = 'AuditLog'
