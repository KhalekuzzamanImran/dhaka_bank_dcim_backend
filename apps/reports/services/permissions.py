from __future__ import annotations

from copy import deepcopy

from django.core.exceptions import PermissionDenied, ValidationError

from apps.common.access import get_access_scope
from apps.common.permissions import user_has_permission
from apps.datacenters.models import DataCenter, Rack, Room
from apps.devices.models import Device
from apps.organizations.models import Organization


def _scope_value(scope: dict, key: str) -> set:
    value = scope.get(key) or set()
    if isinstance(value, set):
        return value
    return set(value)


def _normalized_scope_ids(scope: dict, key: str) -> set[str]:
    return {str(value) for value in _scope_value(scope, key)}


def user_can_access_organization(user, organization_id) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    scope = get_access_scope(user)
    if scope.get("global_access"):
        return True
    return str(organization_id) in _normalized_scope_ids(scope, "organization_ids")


def user_can_access_data_center(user, data_center_id) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    scope = get_access_scope(user)
    if scope.get("global_access"):
        return True
    return str(data_center_id) in _normalized_scope_ids(scope, "data_center_ids")


def ensure_organization_access(user, organization):
    organization_id = getattr(organization, "id", organization)
    if organization_id is None or not user_can_access_organization(user, organization_id):
        raise PermissionDenied("You do not have access to this organization.")
    return organization


def ensure_data_center_access(user, data_center):
    data_center_id = getattr(data_center, "id", data_center)
    if data_center_id is None or not user_can_access_data_center(user, data_center_id):
        raise PermissionDenied("You do not have access to this data center.")
    return data_center


def ensure_template_scope(template, organization, data_center=None):
    errors = {}
    if template.organization_id != organization.id:
        errors.setdefault("template", []).append("Template must belong to the selected organization.")
    if data_center and getattr(template, "data_center_id", None) and template.data_center_id != data_center.id:
        errors.setdefault("template", []).append("Template data center must match the selected data center.")
    if template.definition_id and not template.definition.is_active:
        errors.setdefault("definition", []).append("Selected report definition is inactive.")
    if errors:
        raise ValidationError(errors)
    return template


def ensure_schedule_scope(schedule, organization, data_center=None):
    errors = {}
    if schedule.organization_id != organization.id:
        errors.setdefault("schedule", []).append("Schedule must belong to the selected organization.")
    if data_center and schedule.data_center_id and schedule.data_center_id != data_center.id:
        errors.setdefault("schedule", []).append("Schedule data center must match the selected data center.")
    if errors:
        raise ValidationError(errors)
    return schedule


def resolve_scope_selection(
    *,
    organization,
    data_center=None,
    parameters: dict | None = None,
):
    params = deepcopy(parameters or {})
    selected_rooms = []
    selected_racks = []
    selected_devices = []

    room_ids = [str(value).strip() for value in (params.get("room_ids") or params.get("rooms") or []) if str(value).strip()]
    rack_ids = [str(value).strip() for value in (params.get("rack_ids") or params.get("racks") or []) if str(value).strip()]
    device_ids = [str(value).strip() for value in (params.get("device_ids") or params.get("devices") or []) if str(value).strip()]

    if room_ids:
        selected_rooms = list(
            Room.objects.filter(id__in=room_ids, data_center__organization_id=organization.id).select_related("data_center")
        )
        if data_center:
            selected_rooms = [room for room in selected_rooms if room.data_center_id == data_center.id]
        if len(selected_rooms) != len(set(room_ids)):
            raise ValidationError({"room_ids": "One or more room selections are invalid or inaccessible."})

    if rack_ids:
        selected_racks = list(
            Rack.objects.filter(id__in=rack_ids, room__data_center__organization_id=organization.id).select_related("room", "data_center")
        )
        if data_center:
            selected_racks = [
                rack
                for rack in selected_racks
                if rack.data_center_id == data_center.id or (rack.room_id and rack.room.data_center_id == data_center.id)
            ]
        if len(selected_racks) != len(set(rack_ids)):
            raise ValidationError({"rack_ids": "One or more rack selections are invalid or inaccessible."})

    if device_ids:
        selected_devices = list(
            Device.objects.filter(id__in=device_ids, organization_id=organization.id).select_related(
                "data_center",
                "room__data_center",
                "rack__room__data_center",
            )
        )
        if data_center:
            selected_devices = [device for device in selected_devices if device.data_center_id == data_center.id]
        if len(selected_devices) != len(set(device_ids)):
            raise ValidationError({"device_ids": "One or more device selections are invalid or inaccessible."})

    return {
        "organization": organization,
        "data_center": data_center,
        "selected_rooms": selected_rooms,
        "selected_racks": selected_racks,
        "selected_devices": selected_devices,
    }


def _unique_actions(actions):
    seen = set()
    result = []
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        result.append(action)
    return result


def report_definition_allowed_actions(user, definition=None):
    actions = ["view"]
    return _unique_actions(actions)


def report_template_allowed_actions(user, template):
    actions = ["view"]
    if user_has_permission(user, "report.update"):
        actions.append("edit")
        if getattr(template, "is_active", False):
            actions.append("disable")
    if user_has_permission(user, "report.generate"):
        actions.append("generate")
    return _unique_actions(actions)


def report_schedule_allowed_actions(user, schedule):
    actions = ["view"]
    if user_has_permission(user, "report.update"):
        actions.append("edit")
        if getattr(schedule, "status", None) == "ACTIVE":
            actions.extend(["pause", "disable"])
        elif getattr(schedule, "status", None) == "PAUSED":
            actions.append("resume")
        elif getattr(schedule, "status", None) == "DISABLED":
            actions.append("resume")
    if user_has_permission(user, "report.generate") or user_has_permission(user, "report.update"):
        if getattr(schedule, "status", None) in {"ACTIVE", "PAUSED"}:
            actions.append("run_now")
    actions.extend(["view_runs", "view_deliveries"])
    return _unique_actions(actions)


def report_schedule_run_allowed_actions(user, run=None):
    return ["view"]


def report_job_allowed_actions(user, job):
    actions = ["view"]
    if getattr(job, "can_cancel", False) and user_has_permission(user, "report.update"):
        actions.append("cancel")
    if getattr(job, "can_retry", False) and (user_has_permission(user, "report.generate") or user_has_permission(user, "report.update")):
        actions.append("retry")
    if getattr(job, "is_downloadable", False) and user_has_permission(user, "report.download"):
        actions.append("download")
    actions.append("view_deliveries")
    return _unique_actions(actions)


def report_artifact_allowed_actions(user, artifact):
    actions = ["view"]
    if user_has_permission(user, "report.download"):
        actions.append("download")
    return _unique_actions(actions)


def report_delivery_allowed_actions(user, delivery):
    actions = ["view"]
    if getattr(delivery, "status", None) == "FAILED" and (
        user_has_permission(user, "report.retry") or user_has_permission(user, "report.generate") or user_has_permission(user, "report.update")
    ):
        actions.append("retry")
    return _unique_actions(actions)
