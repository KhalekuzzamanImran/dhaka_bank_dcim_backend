from apps.common.viewsets import ScopedModelViewSet
from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(ScopedModelViewSet):
    http_method_names = ['get','head','options']
    access_scope = 'organization'
    organization_field = 'organization'
    queryset = AuditLog.objects.select_related('organization', 'actor').all().order_by('-created_at')
    serializer_class = AuditLogSerializer
    permission_module = 'audit'
    audit_resource_type = 'AuditLog'
