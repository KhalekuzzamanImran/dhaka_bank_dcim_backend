from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.viewsets import AuditModelViewSet
from .models import Role, Permission, RolePermission, UserDataCenterRole
from .serializers import RoleSerializer, PermissionSerializer, RolePermissionSerializer, UserDataCenterRoleSerializer
class RoleViewSet(AuditModelViewSet):
    queryset = Role.objects.all(); serializer_class = RoleSerializer; permission_module = 'user'; audit_resource_type = 'Role'; filterset_fields = ['scope','status']; search_fields = ['name','code']
class PermissionViewSet(AuditModelViewSet):
    queryset = Permission.objects.all(); serializer_class = PermissionSerializer; permission_module = 'user'; audit_resource_type = 'Permission'; filterset_fields = ['module']; search_fields = ['module','code']
class RolePermissionViewSet(AuditModelViewSet):
    queryset = RolePermission.objects.select_related('role','permission').all(); serializer_class = RolePermissionSerializer; permission_module = 'user'; audit_resource_type = 'RolePermission'; filterset_fields = ['role','permission']
class UserDataCenterRoleViewSet(AuditModelViewSet):
    queryset = UserDataCenterRole.objects.select_related('user','organization','data_center','role').all(); serializer_class = UserDataCenterRoleSerializer; permission_module = 'user'; audit_resource_type = 'UserDataCenterRole'; filterset_fields = ['user','organization','data_center','role','is_active']
    @action(detail=False, methods=['get'])
    def my_access(self, request):
        qs = self.get_queryset().filter(user=request.user, is_active=True)
        return Response(UserDataCenterRoleSerializer(qs, many=True).data)
