from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import ReportTemplate, ReportJob
from .serializers import ReportTemplateSerializer, ReportJobSerializer
class ReportTemplateViewSet(ScopedModelViewSet):
    access_scope = 'organization'
    organization_field = 'id'
    queryset = ReportTemplate.objects.all()
    serializer_class = ReportTemplateSerializer
    permission_module = 'report'
    audit_resource_type = 'ReportTemplate'

class ReportJobViewSet(ScopedModelViewSet):
    access_scope = 'mixed'
    queryset = ReportJob.objects.all()
    serializer_class = ReportJobSerializer
    permission_module = 'report'
    audit_resource_type = 'ReportJob'
