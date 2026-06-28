from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import MaintenanceTicket
from .serializers import MaintenanceTicketSerializer
class MaintenanceTicketViewSet(ScopedModelViewSet):
    queryset = MaintenanceTicket.objects.all()
    serializer_class = MaintenanceTicketSerializer
    permission_module = 'maintenance'
    audit_resource_type = 'MaintenanceTicket'
