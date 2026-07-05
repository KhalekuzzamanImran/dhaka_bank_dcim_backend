"""Shared access helpers for hierarchical DCIM scoping.

The project authorizes users through access rows, not JWT claims. These helpers
compute the effective resource tree for the authenticated user so viewsets and
frontend bootstrap endpoints can reuse the same rules.
"""

from __future__ import annotations

from typing import Dict, Iterable

from django.db.models import Q, QuerySet

from apps.access_control.models import RoleScope, UserResourceAccess
from apps.datacenters.models import DataCenter, Rack, Room
from apps.devices.models import Device
from apps.organizations.models import Organization


def get_user_resource_access_rows(user):
    if not user or not user.is_authenticated:
        return UserResourceAccess.objects.none()
    return UserResourceAccess.objects.select_related(
        "role",
        "organization",
        "data_center",
        "data_center__organization",
        "room",
        "room__data_center",
        "room__data_center__organization",
        "rack",
        "rack__room",
        "rack__room__data_center",
        "rack__room__data_center__organization",
        "rack__data_center",
        "rack__data_center__organization",
        "device",
        "device__organization",
        "device__data_center",
        "device__data_center__organization",
        "device__room",
        "device__room__data_center",
        "device__room__data_center__organization",
        "device__rack",
        "device__rack__room",
        "device__rack__room__data_center",
        "device__rack__room__data_center__organization",
        "device__rack__data_center",
        "device__rack__data_center__organization",
    ).filter(user=user, is_active=True)


def _blank_scope() -> Dict[str, object]:
    return {
        "global_access": False,
        "organization_ids": set(),
        "data_center_ids": set(),
        "room_ids": set(),
        "rack_ids": set(),
        "device_ids": set(),
    }


def _add(scope: Dict[str, object], key: str, value):
    if value is not None:
        scope[key].add(value)


def _add_device(scope: Dict[str, object], device: Device):
    _add(scope, "device_ids", device.id)
    if device.rack_id:
        _add_rack(scope, device.rack)
    if device.room_id:
        _add_room(scope, device.room)
    if device.data_center_id:
        _add_data_center(scope, device.data_center)
    if device.organization_id:
        _add_organization(scope, device.organization)


def _add_rack(scope: Dict[str, object], rack: Rack):
    _add(scope, "rack_ids", rack.id)
    if rack.room_id:
        _add_room(scope, rack.room)
    if rack.data_center_id:
        _add_data_center(scope, rack.data_center)
    elif rack.room_id:
        _add_data_center(scope, rack.room.data_center)


def _add_room(scope: Dict[str, object], room: Room):
    _add(scope, "room_ids", room.id)
    if room.data_center_id:
        _add_data_center(scope, room.data_center)


def _add_data_center(scope: Dict[str, object], data_center: DataCenter):
    _add(scope, "data_center_ids", data_center.id)
    if data_center.organization_id:
        _add_organization(scope, data_center.organization)


def _add_organization(scope: Dict[str, object], organization: Organization):
    _add(scope, "organization_ids", organization.id)


def _expand_row_scope(scope: Dict[str, object], row: UserResourceAccess):
    if row.role and row.role.scope == RoleScope.GLOBAL:
        scope["global_access"] = True
        return

    if row.organization_id:
        _add_organization(scope, row.organization)
        for device in Device.objects.filter(organization_id=row.organization_id).select_related(
            "organization",
            "data_center__organization",
            "room__data_center__organization",
            "rack__room__data_center__organization",
        ):
            _add_device(scope, device)
        for data_center in DataCenter.objects.filter(organization_id=row.organization_id).select_related("organization"):
            _add_data_center(scope, data_center)
            for room in Room.objects.filter(data_center_id=data_center.id).select_related("data_center__organization"):
                _add_room(scope, room)
            for rack in Rack.objects.filter(data_center_id=data_center.id).select_related("room__data_center__organization", "data_center__organization"):
                _add_rack(scope, rack)
        return

    if row.data_center_id:
        _add_data_center(scope, row.data_center)
        for room in Room.objects.filter(data_center_id=row.data_center_id).select_related("data_center__organization"):
            _add_room(scope, room)
        for rack in Rack.objects.filter(data_center_id=row.data_center_id).select_related("room__data_center__organization", "data_center__organization"):
            _add_rack(scope, rack)
        for device in Device.objects.filter(data_center_id=row.data_center_id).select_related(
            "organization",
            "data_center__organization",
            "room__data_center__organization",
            "rack__room__data_center__organization",
        ):
            _add_device(scope, device)
        return

    if row.room_id:
        _add_room(scope, row.room)
        for rack in Rack.objects.filter(room_id=row.room_id).select_related("room__data_center__organization", "data_center__organization"):
            _add_rack(scope, rack)
        for device in Device.objects.filter(room_id=row.room_id).select_related(
            "organization",
            "data_center__organization",
            "room__data_center__organization",
            "rack__room__data_center__organization",
        ):
            _add_device(scope, device)
        return

    if row.rack_id:
        _add_rack(scope, row.rack)
        for device in Device.objects.filter(rack_id=row.rack_id).select_related(
            "organization",
            "data_center__organization",
            "room__data_center__organization",
            "rack__room__data_center__organization",
        ):
            _add_device(scope, device)
        return

    if row.device_id:
        _add_device(scope, row.device)


def get_access_scope(user):
    scope = _blank_scope()
    if not user or not user.is_authenticated:
        return scope
    if user.is_superuser:
        scope["global_access"] = True
        return scope

    rows = list(get_user_resource_access_rows(user))
    if not rows:
        return scope

    for row in rows:
        _expand_row_scope(scope, row)
        if scope["global_access"]:
            break

    return scope


def get_effective_permission_codes(user):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        from apps.access_control.models import Permission

        return set(Permission.objects.values_list("code", flat=True))

    rows = get_user_resource_access_rows(user)
    return set(
        rows.filter(role__role_permissions__permission__code__isnull=False)
        .values_list("role__role_permissions__permission__code", flat=True)
        .distinct()
    )


def _get_accessible_queryset(model, scope, level):
    if scope["global_access"]:
        return model.objects.all()
    key = f"{level}_ids"
    ids = scope[key]
    if not ids:
        return model.objects.none()
    return model.objects.filter(id__in=ids).distinct()


def get_accessible_organizations_for_user(user):
    return _get_accessible_queryset(Organization, get_access_scope(user), "organization")


def get_accessible_data_centers_for_user(user):
    return _get_accessible_queryset(DataCenter, get_access_scope(user), "data_center")


def get_accessible_rooms_for_user(user):
    return _get_accessible_queryset(Room, get_access_scope(user), "room")


def get_accessible_racks_for_user(user):
    return _get_accessible_queryset(Rack, get_access_scope(user), "rack")


def get_accessible_devices_for_user(user):
    return _get_accessible_queryset(Device, get_access_scope(user), "device")


def _lookup_exists(model, lookup_path: str) -> bool:
    if not lookup_path:
        return False
    opts = model._meta
    parts = lookup_path.split("__")
    for part in parts:
        try:
            field = opts.get_field(part)
        except Exception:
            return False
        if field.is_relation:
            opts = field.remote_field.model._meta
        else:
            opts = None
    return True


def _apply_scope_filter(qs: QuerySet, field_path: str, ids: Iterable, lookup_path: str | None = None):
    if not ids:
        return qs.none()
    path = lookup_path or field_path
    if not _lookup_exists(qs.model, path):
        return qs.none()
    lookup = "id__in" if path in {"id", "pk"} else f"{path}_id__in"
    return qs.filter(**{lookup: list(ids)}).distinct()


def filter_queryset_for_user(
    qs: QuerySet,
    user,
    *,
    access_scope: str = "mixed",
    organization_field: str = "organization",
    data_center_field: str = "data_center",
    room_field: str = "room",
    rack_field: str = "rack",
    device_field: str = "device",
):
    if not user or not user.is_authenticated:
        return qs.none()

    scope = get_access_scope(user)
    if scope["global_access"]:
        return qs

    if not any(scope[key] for key in ("organization_ids", "data_center_ids", "room_ids", "rack_ids", "device_ids")):
        return qs.none()

    if access_scope != "mixed":
        field_map = {
            "organization": organization_field,
            "data_center": data_center_field,
            "room": room_field,
            "rack": rack_field,
            "device": device_field,
        }
        return _apply_scope_filter(qs, field_map[access_scope], scope[f"{access_scope}_ids"])

    conditions = Q()
    added = False
    for level, field_path in (
        ("organization", organization_field),
        ("data_center", data_center_field),
        ("room", room_field),
        ("rack", rack_field),
        ("device", device_field),
    ):
        if _lookup_exists(qs.model, field_path) and scope[f"{level}_ids"]:
            conditions |= Q(**{f"{field_path}_id__in": list(scope[f"{level}_ids"])})
            added = True

    if not added:
        return qs.none()
    return qs.filter(conditions).distinct()
