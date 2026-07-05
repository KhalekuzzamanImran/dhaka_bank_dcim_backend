from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import Notification
from .serializers import NotificationSerializer
class NotificationViewSet(ScopedModelViewSet):
    access_scope = 'organization'
    organization_field = 'id'
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_module = 'notification'
    audit_resource_type = 'Notification'
