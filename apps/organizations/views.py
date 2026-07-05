from apps.common.viewsets import ScopedModelViewSet
from .models import Organization
from .serializers import OrganizationSerializer


class OrganizationViewSet(ScopedModelViewSet):
    access_scope = 'organization'
    organization_field = 'id'
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_module = 'organization'
    audit_resource_type = 'Organization'
    filterset_fields = ['status']
    search_fields = ['name', 'code', 'email', 'phone']
    ordering_fields = ['name', 'code', 'created_at']
