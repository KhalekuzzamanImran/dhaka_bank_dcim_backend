from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.serializers import MeSerializer
from apps.common.access import (
    get_accessible_data_centers_for_user,
    get_accessible_devices_for_user,
    get_accessible_organizations_for_user,
    get_accessible_racks_for_user,
    get_accessible_rooms_for_user,
    get_effective_permission_codes,
)
from apps.common.viewsets import AuditModelViewSet
from apps.datacenters.serializers import DataCenterSerializer, RackSerializer, RoomSerializer
from apps.devices.serializers import DeviceSerializer
from apps.organizations.serializers import OrganizationSerializer

from .models import Permission, Role, RolePermission, UserResourceAccess
from .serializers import PermissionSerializer, RolePermissionSerializer, RoleSerializer, UserResourceAccessSerializer


class RoleViewSet(AuditModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_module = "access"
    audit_resource_type = "Role"
    filterset_fields = ["scope", "status"]
    search_fields = ["name", "code"]


class PermissionViewSet(AuditModelViewSet):
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_module = "access"
    audit_resource_type = "Permission"
    filterset_fields = ["module"]
    search_fields = ["module", "code"]


class RolePermissionViewSet(AuditModelViewSet):
    queryset = RolePermission.objects.select_related("role", "permission").all()
    serializer_class = RolePermissionSerializer
    permission_module = "access"
    audit_resource_type = "RolePermission"
    filterset_fields = ["role", "permission"]


class UserResourceAccessViewSet(AuditModelViewSet):
    queryset = UserResourceAccess.objects.select_related(
        "user",
        "organization",
        "data_center",
        "room",
        "rack",
        "device",
        "role",
    ).all()
    serializer_class = UserResourceAccessSerializer
    permission_module = "access"
    audit_resource_type = "UserResourceAccess"
    filterset_fields = ["user", "organization", "data_center", "room", "rack", "device", "role", "is_active"]

    def get_permissions(self):
        if self.action in {"my_access", "access_context"}:
            return [IsAuthenticated()]
        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def my_access(self, request):
        qs = self.get_queryset().filter(user=request.user, is_active=True)
        return Response(UserResourceAccessSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def access_context(self, request):
        """Frontend bootstrap payload for the authenticated user.

        This exposes the user's effective resource tree without requiring access
        management permissions.
        """
        organization_qs = get_accessible_organizations_for_user(request.user)
        data_center_qs = get_accessible_data_centers_for_user(request.user)
        room_qs = get_accessible_rooms_for_user(request.user)
        rack_qs = get_accessible_racks_for_user(request.user)
        device_qs = get_accessible_devices_for_user(request.user)
        permission_codes = sorted(get_effective_permission_codes(request.user))

        return Response(
            {
                "user": MeSerializer(request.user).data,
                "roles": RoleSerializer(
                    Role.objects.filter(user_assignments__user=request.user, user_assignments__is_active=True).distinct().order_by("name"),
                    many=True,
                ).data,
                "permissions": PermissionSerializer(
                    Permission.objects.filter(code__in=permission_codes).order_by("module", "code"),
                    many=True,
                ).data,
                "organizations": OrganizationSerializer(organization_qs.order_by("name"), many=True).data,
                "data_centers": DataCenterSerializer(data_center_qs.order_by("name"), many=True).data,
                "rooms": RoomSerializer(room_qs.order_by("name"), many=True).data,
                "racks": RackSerializer(rack_qs.order_by("name"), many=True).data,
                "devices": DeviceSerializer(device_qs.order_by("name"), many=True).data,
            }
        )


# Backward-compatible aliases for existing imports and legacy URLs.
UserDataCenterRoleViewSet = UserResourceAccessViewSet
