from rest_framework.permissions import SAFE_METHODS, IsAuthenticated

from apps.common.access import get_effective_permission_codes, get_access_scope

ACTION_PERMISSION_MAP = {
    "list": "view",
    "retrieve": "view",
    "create": "create",
    "update": "update",
    "partial_update": "update",
    "destroy": "delete",
    "acknowledge": "acknowledge",
    "resolve": "resolve",
    "ingest": "create",
    "latest_summary": "view",
    "history": "view",
    "summary": "view",
}


class DCIMRBACPermission(IsAuthenticated):
    """DRF permission class backed by the hierarchical access tables.

    Superusers are always allowed. Other users need a permission code matching
    the view's ``permission_module`` plus the mapped action.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.is_superuser:
            return True

        permission_module = getattr(view, "permission_module", None)
        if not permission_module:
            return True

        action = getattr(view, "action", None) or ("view" if request.method in SAFE_METHODS else "update")
        if permission_module == "access":
            verb = "view" if action in {"list", "retrieve"} else "manage"
        else:
            verb = ACTION_PERMISSION_MAP.get(action, "view" if request.method in SAFE_METHODS else "update")
        code = f"{permission_module}.{verb}"
        return user_has_permission(request.user, code)


def user_has_permission(user, permission_code):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return permission_code in get_effective_permission_codes(user)


def allowed_data_center_ids(user):
    if not user or not user.is_authenticated:
        return []
    if user.is_superuser:
        return None
    scope = get_access_scope(user)
    if scope["global_access"]:
        return None
    return list(scope["data_center_ids"])
