from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.audit import write_audit

from ..enums import ReportTriggerSource
from ..models import (
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleRun,
    ReportScheduleRunStatus,
)
from .definitions import validate_definition_request
from .permissions import ensure_data_center_access, ensure_organization_access, ensure_schedule_scope, ensure_template_scope
from .templates import build_report_template_snapshot


@dataclass(frozen=True)
class ReportJobFactoryResult:
    job: ReportJob
    schedule_run: ReportScheduleRun | None = None
    created: bool = True
    duplicate: bool = False
    queued: bool = False


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        raise ValidationError({"idempotency_key": "Idempotency key cannot be blank."})
    return candidate


def _definition_default_parameters(definition) -> dict:
    if definition is None:
        return {}
    schema = definition.parameter_schema if isinstance(definition.parameter_schema, dict) else {}
    defaults = schema.get("default_parameters")
    if isinstance(defaults, dict):
        return deepcopy(defaults)
    return {}


def _parse_datetime_value(value):
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value
    if isinstance(value, str):
        return parse_datetime(value)
    return value


def build_parameters_snapshot(parameters: dict | None) -> dict:
    return deepcopy(parameters or {})


def build_template_snapshot(template) -> dict:
    if not template:
        return {}
    return build_report_template_snapshot(template)


def build_scope_snapshot(*, organization, data_center=None, parameters=None) -> dict:
    parameters = deepcopy(parameters or {})
    scope = {
        "organization": {
            "id": str(organization.id) if organization else None,
            "name": getattr(organization, "name", None),
            "code": getattr(organization, "code", None),
        },
        "data_center": None,
        "selected_rooms": [],
        "selected_racks": [],
        "selected_devices": [],
    }
    if data_center is not None:
        scope["data_center"] = {
            "id": str(data_center.id),
            "name": getattr(data_center, "name", None),
            "code": getattr(data_center, "code", None),
        }

    from .permissions import resolve_scope_selection

    selection = resolve_scope_selection(organization=organization, data_center=data_center, parameters=parameters)
    for room in selection["selected_rooms"]:
        scope["selected_rooms"].append({"id": str(room.id), "name": room.name, "code": getattr(room, "code", None)})
    for rack in selection["selected_racks"]:
        scope["selected_racks"].append({"id": str(rack.id), "name": rack.name, "code": getattr(rack, "code", None)})
    for device in selection["selected_devices"]:
        scope["selected_devices"].append({"id": str(device.id), "name": device.name, "code": getattr(device, "code", None)})
    return scope


def build_output_config_snapshot(*, definition, template=None, schedule=None, primary_format=None, attachment_formats=None) -> dict:
    if template:
        primary_format = primary_format or template.primary_format
        attachment_formats = attachment_formats if attachment_formats is not None else template.attachment_formats
    elif schedule:
        primary_format = primary_format or schedule.primary_format
        attachment_formats = attachment_formats if attachment_formats is not None else schedule.attachment_formats
    return {
        "definition_code": getattr(definition, "code", None),
        "report_type": getattr(template, "report_type", None) or getattr(schedule, "report_type", None) or getattr(definition, "code", None),
        "primary_format": primary_format,
        "attachment_formats": deepcopy(attachment_formats or []),
        "include_charts": bool(getattr(template, "include_charts", False)),
        "include_raw_data": bool(getattr(template, "include_raw_data", False) if template else getattr(schedule, "attach_raw_data", False)),
    }


def build_recipient_snapshot(*, schedule=None, recipients=None) -> dict:
    if schedule is not None:
        return {
            "email_recipients": deepcopy(schedule.normalize_recipients()),
            "sms_recipients": deepcopy(schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []),
            "send_sms": schedule.send_sms,
        }
    return {"recipients": deepcopy(recipients or [])}


def build_source_event_snapshot(source_event) -> dict:
    return deepcopy(source_event or {})


def _merge_parameters(definition, template, schedule, parameters, runtime_parameters):
    merged = {}
    merged.update(_definition_default_parameters(definition))
    if template and isinstance(template.default_parameters, dict):
        merged.update(deepcopy(template.default_parameters))
    if schedule and isinstance(schedule.parameter_overrides, dict):
        merged.update(deepcopy(schedule.parameter_overrides))
    if parameters:
        merged.update(deepcopy(parameters))
    if runtime_parameters:
        merged.update(deepcopy(runtime_parameters))
    return merged


def _schedule_window_key(trigger_source: str, schedule: ReportSchedule | None, scheduled_for):
    if trigger_source == ReportTriggerSource.SCHEDULED and schedule is not None and scheduled_for is not None:
        return (str(schedule.pk), scheduled_for)
    return None


@transaction.atomic
def create_report_job(
    *,
    definition,
    organization,
    actor=None,
    data_center=None,
    template=None,
    schedule=None,
    trigger_source,
    requested_by=None,
    parameters=None,
    runtime_parameters=None,
    recipients=None,
    source_event=None,
    idempotency_key=None,
    scheduled_for=None,
    window_start=None,
    window_end=None,
    queue_job=True,
    compatibility_mode=False,
):
    resolved_definition = definition
    if resolved_definition is None and template is not None and getattr(template, "report_type", None):
        from .definitions import get_active_definition_by_code, get_definition_code_for_report_type

        definition_code = get_definition_code_for_report_type(template.report_type)
        resolved_definition = get_active_definition_by_code(definition_code) if definition_code else None

    legacy_compatibility_mode = schedule is not None and template is None and resolved_definition is None
    if resolved_definition is None and not legacy_compatibility_mode:
        raise ValidationError({"definition": "A report definition is required."})
    if resolved_definition is not None and not resolved_definition.is_active:
        raise ValidationError({"definition": "Selected report definition is inactive."})
    if actor is not None:
        ensure_organization_access(actor, organization)
        if data_center is not None:
            ensure_data_center_access(actor, data_center)
    if data_center is not None and data_center.organization_id != organization.id:
        raise ValidationError({"data_center": "Data center must belong to the selected organization."})
    if template is not None:
        ensure_template_scope(template, organization, data_center)
    if schedule is not None:
        ensure_schedule_scope(schedule, organization, data_center)

    trigger_source = str(trigger_source or ReportTriggerSource.MANUAL).upper()
    if trigger_source not in ReportTriggerSource.values:
        raise ValidationError({"trigger_source": "Unsupported trigger source."})

    normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)

    if template and template.definition_id and resolved_definition is not None and template.definition_id != resolved_definition.id:
        raise ValidationError({"definition": "Definition must match the selected template."})
    if schedule and schedule.template_id and template and schedule.template_id != template.id:
        raise ValidationError({"schedule": "Schedule template must match the selected template."})

    merged_parameters = _merge_parameters(definition, template, schedule, parameters, runtime_parameters)
    runtime_parameters = deepcopy(runtime_parameters or {})

    template_snapshot = build_template_snapshot(template)
    parameters_snapshot = build_parameters_snapshot(merged_parameters)
    scope_snapshot = build_scope_snapshot(organization=organization, data_center=data_center, parameters=merged_parameters)
    output_config_snapshot = build_output_config_snapshot(
        definition=definition,
        template=template,
        schedule=schedule,
        primary_format=runtime_parameters.get("primary_format") or getattr(template, "primary_format", None) or getattr(schedule, "primary_format", None),
        attachment_formats=runtime_parameters.get("attachment_formats"),
    )
    recipient_snapshot = build_recipient_snapshot(schedule=schedule, recipients=recipients)
    source_event_snapshot = build_source_event_snapshot(source_event)

    if template is not None and not compatibility_mode:
        validate_definition_request(
            resolved_definition or definition,
            organization,
            data_center,
            merged_parameters,
            output_config_snapshot.get("primary_format"),
            output_config_snapshot.get("attachment_formats"),
            deepcopy(runtime_parameters.get("delivery_channels") or []),
        )

    existing_job = None
    if normalized_idempotency_key:
        if trigger_source == ReportTriggerSource.MANUAL and requested_by is not None:
            existing_job = (
                ReportJob.objects.filter(
                    organization=organization,
                    requested_by=requested_by,
                    trigger_source=trigger_source,
                    idempotency_key=normalized_idempotency_key,
                )
                .select_related("organization", "data_center", "template", "schedule", "requested_by")
                .first()
            )
        elif trigger_source == ReportTriggerSource.EVENT:
            existing_job = (
                ReportJob.objects.filter(
                    organization=organization,
                    definition=definition,
                    trigger_source=trigger_source,
                    idempotency_key=normalized_idempotency_key,
                )
                .select_related("organization", "data_center", "template", "schedule", "requested_by")
                .first()
            )
    if existing_job:
        return ReportJobFactoryResult(job=existing_job, schedule_run=None, created=False, duplicate=True, queued=False)

    schedule_run = None
    if schedule is not None:
        window_start = _parse_datetime_value(window_start) or timezone.now()
        window_end = _parse_datetime_value(window_end) or timezone.now()
        scheduled_for = _parse_datetime_value(scheduled_for)
        if trigger_source == ReportTriggerSource.SCHEDULED and scheduled_for is None:
            scheduled_for = window_end
        run_defaults = {
            "organization": organization,
            "requested_by": requested_by,
            "window_start": window_start,
            "window_end": window_end,
            "status": ReportScheduleRunStatus.PENDING,
            "queued_at": timezone.now(),
            "trigger_source": trigger_source,
            "snapshot": {
                "schedule_id": str(schedule.pk),
                "schedule_name": schedule.name,
                "trigger_source": trigger_source,
                "scheduled_for": scheduled_for.isoformat() if hasattr(scheduled_for, "isoformat") else scheduled_for,
                "parameters": deepcopy(merged_parameters),
            },
        }
        try:
            schedule_run, created_run = ReportScheduleRun.objects.get_or_create(
                schedule=schedule,
                scheduled_for=scheduled_for if trigger_source == ReportTriggerSource.SCHEDULED else None,
                trigger_source=trigger_source,
                defaults=run_defaults,
            )
        except IntegrityError:
            schedule_run = (
                ReportScheduleRun.objects.select_related("job", "generated_job")
                .filter(schedule=schedule, scheduled_for=scheduled_for if trigger_source == ReportTriggerSource.SCHEDULED else None, trigger_source=trigger_source)
                .first()
            )
        if schedule_run and schedule_run.job_id:
            existing = schedule_run.job or schedule_run.generated_job
            if existing:
                return ReportJobFactoryResult(job=existing, schedule_run=schedule_run, created=False, duplicate=True, queued=False)

    job_kwargs = {
        "organization": organization,
        "data_center": data_center,
        "definition": resolved_definition or definition,
        "template": template,
        "schedule": schedule,
        "requested_by": requested_by,
        "status": ReportJobStatus.PENDING,
        "parameters": merged_parameters,
        "parameters_snapshot": parameters_snapshot,
        "template_snapshot": template_snapshot,
        "template_config_snapshot": deepcopy(template.config if template and isinstance(template.config, dict) else {}),
        "output_config_snapshot": output_config_snapshot,
        "scope_snapshot": scope_snapshot,
        "recipient_snapshot": recipient_snapshot,
        "source_event_snapshot": source_event_snapshot,
        "trigger_source": trigger_source,
        "idempotency_key": normalized_idempotency_key,
    }

    job = ReportJob.objects.create(**job_kwargs)

    if schedule_run is not None and not schedule_run.job_id:
        schedule_run.job = job
        schedule_run.generated_job = job
        schedule_run.save(update_fields=["job", "generated_job", "updated_at"])

    _safe_write_audit(
        "REPORT_JOB_CREATED" if trigger_source == ReportTriggerSource.MANUAL else "REPORT_SCHEDULED_JOB_CREATED" if trigger_source == ReportTriggerSource.SCHEDULED else "REPORT_EVENT_REQUESTED",
        "ReportJob",
        job.pk,
        organization=organization,
        actor=requested_by,
        message="Report job created",
        new_value={
            "definition_code": (resolved_definition or definition).code if (resolved_definition or definition) else None,
            "template_id": str(template.pk) if template else None,
            "template_version": getattr(template, "version", None),
            "schedule_id": str(schedule.pk) if schedule else None,
            "trigger_source": trigger_source,
            "organization_id": str(organization.pk) if organization else None,
            "data_center_id": str(data_center.pk) if data_center else None,
        },
    )

    queued = False
    if queue_job:
        from ..tasks import generate_report_job_task

        def _queue_generation():
            generate_report_job_task.delay(str(job.pk))

        transaction.on_commit(_queue_generation)
        queued = True

    return ReportJobFactoryResult(job=job, schedule_run=schedule_run, created=True, duplicate=False, queued=queued)
