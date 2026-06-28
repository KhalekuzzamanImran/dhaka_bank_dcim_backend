from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import Dashboard, DashboardWidget
from .serializers import DashboardSerializer, DashboardWidgetSerializer
class DashboardViewSet(ScopedModelViewSet):
    queryset = Dashboard.objects.all()
    serializer_class = DashboardSerializer
    permission_module = 'dashboard'
    audit_resource_type = 'Dashboard'

class DashboardWidgetViewSet(ScopedModelViewSet):
    queryset = DashboardWidget.objects.all()
    serializer_class = DashboardWidgetSerializer
    permission_module = 'dashboard'
    audit_resource_type = 'DashboardWidget'
