from __future__ import annotations

from apps.common.access import get_access_scope

from ..models import ReportDefinition, ReportJob, ReportSchedule, ReportTemplate


def _scope_ids(scope: dict, key: str) -> set:
    value = scope.get(key) or set()
    if isinstance(value, set):
        return value
    return set(value)


def get_active_definitions_queryset():
    return ReportDefinition.objects.filter(is_active=True).order_by("category", "name")


def get_active_definition_by_code(code: str | None):
    if not code:
        return None
    normalized = str(code).strip()
    if not normalized:
        return None
    return get_active_definitions_queryset().filter(code__iexact=normalized).first()


def get_definition_by_code(code: str | None):
    if not code:
        return None
    normalized = str(code).strip()
    if not normalized:
        return None
    return ReportDefinition.objects.filter(code__iexact=normalized).first()


def _visible_queryset(user, model, *, organization_field="organization", data_center_field=None):
    if not user or not getattr(user, "is_authenticated", False):
        return model.objects.none()
    if getattr(user, "is_superuser", False):
        return model.objects.all()
    scope = get_access_scope(user)
    if scope.get("global_access"):
        return model.objects.all()

    qs = model.objects.all()
    if organization_field:
        org_ids = _scope_ids(scope, "organization_ids")
        if org_ids:
            qs = qs.filter(**{f"{organization_field}__in": org_ids})
        else:
            return model.objects.none()
    if data_center_field:
        dc_ids = _scope_ids(scope, "data_center_ids")
        if dc_ids:
            qs = qs.filter(**{f"{data_center_field}__in": dc_ids})
        else:
            return model.objects.none()
    return qs.distinct()


def get_visible_templates_queryset(user):
    return _visible_queryset(user, ReportTemplate, organization_field="organization", data_center_field=None)


def get_visible_schedules_queryset(user):
    return _visible_queryset(user, ReportSchedule, organization_field="organization", data_center_field="data_center")


def get_visible_jobs_queryset(user):
    return _visible_queryset(user, ReportJob, organization_field="organization", data_center_field="data_center")


def get_template_for_user(user, template_id):
    return get_visible_templates_queryset(user).filter(pk=template_id).first()


def get_schedule_for_user(user, schedule_id):
    return get_visible_schedules_queryset(user).filter(pk=schedule_id).first()


def get_job_for_user(user, job_id):
    return get_visible_jobs_queryset(user).filter(pk=job_id).first()
