from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ValidationError

from apps.common.access import get_access_scope, get_effective_permission_codes
from apps.reports.domain import ReportJobStatus, ReportScheduleStatus
from apps.reports.models import ReportDefinition

SCHEDULE_ACTION_RUN_NOW = "run_now"
SCHEDULE_ACTION_EDIT = "edit"
SCHEDULE_ACTION_PAUSE = "pause"
SCHEDULE_ACTION_RESUME = "resume"
SCHEDULE_ACTION_DELETE = "delete"
SCHEDULE_ACTION_VIEW_EXECUTIONS = "view_executions"
SCHEDULE_ACTION_VIEW_DELIVERIES = "view_deliveries"

SCHEDULE_ACTION_PERMISSION_MAP = {
    SCHEDULE_ACTION_RUN_NOW: "report.generate",
    SCHEDULE_ACTION_EDIT: "report.update",
    SCHEDULE_ACTION_PAUSE: "report.update",
    SCHEDULE_ACTION_RESUME: "report.update",
    SCHEDULE_ACTION_DELETE: "report.delete",
    SCHEDULE_ACTION_VIEW_EXECUTIONS: "report.view",
    SCHEDULE_ACTION_VIEW_DELIVERIES: "report.view",
}


@dataclass(frozen=True)
class ScheduleActionPolicy:
    allowed_actions: tuple[str, ...]
    denied_actions: dict[str, tuple[int, str, str]] = field(default_factory=dict)

    def denial(self, action: str) -> tuple[int, str, str]:
        return self.denied_actions.get(action, (403, "SCHEDULE_ACTION_NOT_ALLOWED", "You do not have permission to perform this action."))

    def can(self, action: str) -> bool:
        return action in self.allowed_actions


def _request_cache(request: Any | None) -> dict[str, Any] | None:
    if request is None:
        return None
    cache = getattr(request, "_report_schedule_action_cache", None)
    if cache is None:
        cache = {}
        setattr(request, "_report_schedule_action_cache", cache)
    return cache


def _permission_codes_for_user(user, request: Any | None = None):
    cache = _request_cache(request)
    if cache is not None and "permission_codes" in cache:
        return cache["permission_codes"]
    codes = get_effective_permission_codes(user)
    if cache is not None:
        cache["permission_codes"] = codes
    return codes


def _access_scope_for_user(user, request: Any | None = None):
    cache = _request_cache(request)
    if cache is not None and "access_scope" in cache:
        return cache["access_scope"]
    scope = get_access_scope(user)
    if cache is not None:
        cache["access_scope"] = scope
    return scope


def _schedule_has_executions(schedule) -> bool:
    if getattr(schedule, "last_job_id", None):
        return True
    executions = getattr(schedule, "_prefetched_objects_cache", {}).get("executions")
    if executions is not None:
        return bool(executions)
    return False


def _schedule_has_blocking_job(schedule) -> bool:
    job = getattr(schedule, "last_job", None)
    if job is None and getattr(schedule, "last_job_id", None):
        job = schedule.last_job
    if not job:
        return False
    return job.status in {ReportJobStatus.QUEUED, ReportJobStatus.RUNNING, ReportJobStatus.PENDING, ReportJobStatus.PROCESSING}


def _resolve_schedule_definition(schedule):
    definition = getattr(schedule, "definition", None)
    if definition and getattr(definition, "code", None):
        return definition

    definition_code = getattr(schedule, "definition_code", None) or getattr(schedule, "report_type", None)
    if definition_code:
        resolved_code = str(definition_code).strip().lower()
        if resolved_code:
            definition = ReportDefinition.objects.filter(code=resolved_code).first()
            if definition:
                return definition

    summary = getattr(schedule, "definition_summary", None)
    if isinstance(summary, dict):
      summary_code = summary.get("code")
      if summary_code:
        return ReportDefinition.objects.filter(code=str(summary_code).strip().lower()).first()

    return getattr(schedule, "definition", None)


def _build_denial(status_code: int, code: str, message: str):
    return status_code, code, message


def get_schedule_action_policy(*, schedule, user, organization=None, data_center=None, request=None) -> ScheduleActionPolicy:
    if not user or not getattr(user, "is_authenticated", False):
        denied = {
            action: _build_denial(403, "SCHEDULE_ACTION_NOT_AUTHENTICATED", "Authentication is required.")
            for action in SCHEDULE_ACTION_PERMISSION_MAP
        }
        return ScheduleActionPolicy(allowed_actions=tuple(), denied_actions=denied)

    if getattr(user, "is_superuser", False):
        permission_codes = None
        access_scope = None
    else:
        permission_codes = _permission_codes_for_user(user, request=request)
        access_scope = _access_scope_for_user(user, request=request)

    schedule_organization_id = getattr(schedule, "organization_id", None) or getattr(organization, "id", organization)
    schedule_data_center_id = getattr(schedule, "data_center_id", None) or getattr(data_center, "id", data_center)

    scope_allowed = True
    if not getattr(user, "is_superuser", False):
        if access_scope is not None and not access_scope.get("global_access"):
            if schedule_organization_id and schedule_organization_id not in access_scope.get("organization_ids", set()):
                scope_allowed = False
            if scope_allowed and schedule_data_center_id and schedule_data_center_id not in access_scope.get("data_center_ids", set()):
                scope_allowed = False

    if not scope_allowed:
        denied = {
            action: _build_denial(403, "SCHEDULE_SCOPE_FORBIDDEN", "You do not have access to this schedule.")
            for action in SCHEDULE_ACTION_PERMISSION_MAP
        }
        return ScheduleActionPolicy(allowed_actions=tuple(), denied_actions=denied)

    allowed_actions: list[str] = []
    denied_actions: dict[str, tuple[int, str, str]] = {}

    def _has_permission(permission_code: str) -> bool:
        if permission_codes is None:
            return True
        return permission_code in permission_codes

    def _add_allowed(action: str):
        if action not in allowed_actions:
            allowed_actions.append(action)

    def _deny(action: str, status_code: int, code: str, message: str):
        denied_actions[action] = _build_denial(status_code, code, message)

    status = getattr(schedule, "status", None)
    definition = _resolve_schedule_definition(schedule)
    template = getattr(schedule, "template", None)

    if _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_VIEW_EXECUTIONS]):
        _add_allowed(SCHEDULE_ACTION_VIEW_EXECUTIONS)
    else:
        _deny(SCHEDULE_ACTION_VIEW_EXECUTIONS, 403, "SCHEDULE_VIEW_FORBIDDEN", "You do not have permission to view schedule executions.")

    if _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_VIEW_DELIVERIES]):
        _add_allowed(SCHEDULE_ACTION_VIEW_DELIVERIES)
    else:
        _deny(SCHEDULE_ACTION_VIEW_DELIVERIES, 403, "SCHEDULE_VIEW_FORBIDDEN", "You do not have permission to view schedule deliveries.")

    edit_permission = _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_EDIT])
    can_edit = status in {ReportScheduleStatus.ACTIVE, ReportScheduleStatus.PAUSED, ReportScheduleStatus.DISABLED}
    if edit_permission and can_edit:
        _add_allowed(SCHEDULE_ACTION_EDIT)
    elif not edit_permission:
        _deny(SCHEDULE_ACTION_EDIT, 403, "SCHEDULE_EDIT_FORBIDDEN", "You do not have permission to edit this schedule.")
    else:
        _deny(
            SCHEDULE_ACTION_EDIT,
            409,
            "SCHEDULE_NOT_EDITABLE",
            "This schedule cannot be edited in its current state.",
        )

    pause_permission = _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_PAUSE])
    if pause_permission and status == ReportScheduleStatus.ACTIVE:
        _add_allowed(SCHEDULE_ACTION_PAUSE)
    elif not pause_permission:
        _deny(SCHEDULE_ACTION_PAUSE, 403, "SCHEDULE_PAUSE_FORBIDDEN", "You do not have permission to pause this schedule.")
    else:
        _deny(
            SCHEDULE_ACTION_PAUSE,
            409,
            "SCHEDULE_NOT_PAUSABLE",
            "This schedule cannot be paused in its current state.",
        )

    resume_permission = _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_RESUME])
    if resume_permission and status == ReportScheduleStatus.PAUSED:
        _add_allowed(SCHEDULE_ACTION_RESUME)
    elif not resume_permission:
        _deny(SCHEDULE_ACTION_RESUME, 403, "SCHEDULE_RESUME_FORBIDDEN", "You do not have permission to resume this schedule.")
    else:
        _deny(
            SCHEDULE_ACTION_RESUME,
            409,
            "SCHEDULE_NOT_RESUMABLE",
            "This schedule cannot be resumed in its current state.",
        )

    delete_permission = _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_DELETE])
    delete_allowed = not _schedule_has_executions(schedule)
    if delete_permission and delete_allowed:
        _add_allowed(SCHEDULE_ACTION_DELETE)
    elif not delete_permission:
        _deny(SCHEDULE_ACTION_DELETE, 403, "SCHEDULE_DELETE_FORBIDDEN", "You do not have permission to delete this schedule.")
    else:
        _deny(
            SCHEDULE_ACTION_DELETE,
            409,
            "SCHEDULE_HAS_EXECUTIONS",
            "Schedule has existing executions. Disable it instead of deleting.",
        )

    run_now_permission = _has_permission(SCHEDULE_ACTION_PERMISSION_MAP[SCHEDULE_ACTION_RUN_NOW])
    runnable = (
        status == ReportScheduleStatus.ACTIVE
        and getattr(schedule, "is_active", True)
        and bool(definition and getattr(definition, "is_active", True))
        and bool(definition and getattr(definition, "supports_scheduling", True))
        and bool(template and getattr(template, "is_active", True))
        and not _schedule_has_blocking_job(schedule)
    )
    if run_now_permission and runnable:
        _add_allowed(SCHEDULE_ACTION_RUN_NOW)
    elif not run_now_permission:
        _deny(
            SCHEDULE_ACTION_RUN_NOW,
            403,
            "SCHEDULE_RUN_NOT_ALLOWED",
            "You do not have permission to run this schedule.",
        )
    else:
        _deny(
            SCHEDULE_ACTION_RUN_NOW,
            409,
            "SCHEDULE_NOT_RUNNABLE",
            "This schedule cannot be run in its current state.",
        )

    return ScheduleActionPolicy(allowed_actions=tuple(allowed_actions), denied_actions=denied_actions)


def get_schedule_allowed_actions(*, schedule, user, organization=None, data_center=None, request=None) -> list[str]:
    return list(
        get_schedule_action_policy(
            schedule=schedule,
            user=user,
            organization=organization,
            data_center=data_center,
            request=request,
        ).allowed_actions
    )


def validate_report_scope(*, user, organization, data_center=None):
    if not user or not user.is_authenticated:
        raise ValidationError({"organization": "Authentication is required."})
    if getattr(user, "is_superuser", False):
        return
    scope = get_access_scope(user)
    organization_id = getattr(organization, "id", organization)
    data_center_id = getattr(data_center, "id", data_center) if data_center else None
    if scope["global_access"]:
        return
    if organization_id and organization_id not in scope["organization_ids"]:
        raise ValidationError({"organization": "You do not have access to this organization."})
    if data_center_id and data_center_id not in scope["data_center_ids"]:
        raise ValidationError({"data_center": "You do not have access to this data center."})
