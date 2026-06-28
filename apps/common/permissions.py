from rest_framework.permissions import BasePermission, SAFE_METHODS, IsAuthenticated

ACTION_PERMISSION_MAP = {
    'list': 'view', 'retrieve': 'view',
    'create': 'create', 'update': 'update', 'partial_update': 'update',
    'destroy': 'delete',
    'acknowledge': 'acknowledge', 'resolve': 'resolve',
    'ingest': 'create', 'latest_summary': 'view', 'history': 'view', 'summary': 'view',
}

class DCIMRBACPermission(IsAuthenticated):
    """DRF permission class backed by the custom Role/Permission tables.

    Superusers are always allowed. Other users need a permission code matching
    <view.permission_module>.<mapped_action>. Example: devices.view.
    """
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.is_superuser:
            return True
        permission_module = getattr(view, 'permission_module', None)
        if not permission_module:
            return True
        action = getattr(view, 'action', None) or ('view' if request.method in SAFE_METHODS else 'update')
        verb = ACTION_PERMISSION_MAP.get(action, 'view' if request.method in SAFE_METHODS else 'update')
        code = f'{permission_module}.{verb}'
        return user_has_permission(request.user, code)

def user_has_permission(user, permission_code):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from apps.access_control.models import UserDataCenterRole
    return UserDataCenterRole.objects.filter(
        user=user, is_active=True, role__role_permissions__permission__code=permission_code
    ).exists()

def allowed_data_center_ids(user):
    if user.is_superuser:
        return None
    from apps.access_control.models import UserDataCenterRole
    qs = UserDataCenterRole.objects.filter(user=user, is_active=True)
    if qs.filter(data_center__isnull=True).exists():
        return None
    return list(qs.exclude(data_center=None).values_list('data_center_id', flat=True).distinct())
