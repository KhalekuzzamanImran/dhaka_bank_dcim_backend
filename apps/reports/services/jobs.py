from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.audit import write_audit
from apps.reports.domain import ReportJobTriggerSource
from apps.reports.models import ReportDefinition, ReportJob, ReportJobStatus, ReportSchedule, ReportTemplate

from .parameters import normalize_report_parameters
from .permissions import validate_report_scope
from .snapshots import build_execution_snapshots

logger = logging.getLogger(__name__)


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write report audit log.", exc_info=True)
        return None


@dataclass(frozen=True)
class CreatedReportJob:
    job: ReportJob
    queued: bool


class ReportJobService:
    @staticmethod
    def _resolve_supported_primary_format(definition: ReportDefinition, requested_format):
        supported_formats = list(definition.supported_formats or [])
        normalized_supported = [str(fmt).upper() for fmt in supported_formats if fmt]
        requested = str(requested_format).upper() if requested_format else None
        if requested and requested in normalized_supported:
            return requested
        if normalized_supported:
            return normalized_supported[0]
        return requested or "CSV"

    @staticmethod
    def _resolve_supported_attachment_formats(definition: ReportDefinition, requested_formats):
        normalized_supported = {str(fmt).upper() for fmt in (definition.supported_formats or []) if fmt}
        normalized = []
        for fmt in requested_formats or []:
            candidate = str(fmt).upper().strip()
            if not candidate:
                continue
            if normalized_supported and candidate not in normalized_supported:
                continue
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @classmethod
    def _validate_definition_template(cls, *, organization, data_center, definition, template, trigger_source):
        if not isinstance(definition, ReportDefinition):
            raise ValueError("Report definition is required.")
        if not definition.is_active:
            raise ValueError("Definition is inactive.")
        if not isinstance(template, ReportTemplate):
            raise ValueError("Report template is required.")
        if not template.is_active:
            raise ValueError("Template is inactive.")
        if template.definition_id and template.definition_id != definition.id:
            raise ValueError("Template definition must match the selected definition.")
        if template.organization_id not in {None, getattr(organization, "id", organization)}:
            raise ValueError("Template must belong to the selected organization or be global.")
        return True

    @classmethod
    def create_and_enqueue(
        cls,
        *,
        organization,
        data_center,
        definition,
        template,
        parameters,
        primary_format,
        attachment_formats,
        trigger_source,
        requested_by=None,
        schedule=None,
        scheduled_for=None,
        recipients=None,
        parent_job=None,
        enqueue: bool = True,
    ) -> ReportJob:
        parameters = normalize_report_parameters(parameters)
        if trigger_source not in ReportJobTriggerSource.values:
            raise ValueError("Unsupported trigger source.")
        if schedule and schedule.organization_id != getattr(organization, "id", organization):
            raise ValueError("Schedule must belong to the selected organization.")
        if schedule and schedule.definition_id and definition and schedule.definition_id != definition.id:
            raise ValueError("Schedule definition must match the selected definition.")
        if schedule and schedule.template_id and template and schedule.template_id != template.id:
            raise ValueError("Schedule template must match the selected template.")
        primary_format = cls._resolve_supported_primary_format(definition, primary_format)
        attachment_formats = cls._resolve_supported_attachment_formats(definition, attachment_formats)

        if requested_by is not None:
            validate_report_scope(user=requested_by, organization=organization, data_center=data_center)

        snapshots = build_execution_snapshots(
            organization=organization,
            data_center=data_center,
            definition=definition,
            template=template,
            schedule=schedule,
            parameters=parameters,
            recipients=recipients or (schedule.recipient_entries.filter(is_active=True) if schedule and hasattr(schedule, "recipient_entries") else []),
            requested_by=requested_by,
            primary_format=primary_format,
            attachment_formats=attachment_formats,
        )

        job_defaults = {
            "organization": organization,
            "data_center": data_center,
            "definition": definition,
            "template": template,
            "trigger_source": trigger_source,
            "requested_by": requested_by,
            "status": ReportJobStatus.QUEUED,
            "queued_at": timezone.now(),
            "parameters": parameters,
            **{k: v for k, v in snapshots.items() if k in {
                "definition_code_snapshot",
                "definition_version_snapshot",
                "template_name_snapshot",
                "template_version_snapshot",
                "template_config_snapshot",
                "parameters_snapshot",
                "scope_snapshot",
                "recipient_snapshot",
                "output_config_snapshot",
            }},
        }
        with transaction.atomic():
            if schedule is not None and scheduled_for is not None:
                try:
                    job, created = ReportJob.objects.get_or_create(
                        schedule=schedule,
                        scheduled_for=scheduled_for,
                        defaults=job_defaults,
                    )
                    if not created:
                        return job
                except IntegrityError:
                    job = ReportJob.objects.filter(schedule=schedule, scheduled_for=scheduled_for).first()
                    if job:
                        return job
                    raise
            else:
                job = ReportJob.objects.create(
                    scheduled_for=scheduled_for,
                    **job_defaults,
                )
            if parent_job:
                job.parent_job = parent_job
                job.retry_count = (parent_job.retry_count or 0) + 1
                job.save(update_fields=["parent_job", "retry_count", "updated_at"])

            if enqueue:
                from apps.reports.tasks import generate_report_job_task

                transaction.on_commit(lambda: generate_report_job_task.delay(str(job.pk)))
            _safe_write_audit(
                "REPORT_JOB_QUEUED",
                "ReportJob",
                job.pk,
                organization=organization,
                actor=requested_by,
                message=f"Report job queued for {getattr(definition, 'code', None)}",
            )
        return job

    @classmethod
    def enqueue_job(cls, job: ReportJob, *, requested_by=None):
        if job.status == ReportJobStatus.FAILED:
            job.error_message = ""
            job.error_code = ""
        if job.file:
            job.file.delete(save=False)
            job.file = None
        job.status = ReportJobStatus.QUEUED
        job.started_at = None
        job.completed_at = None
        job.save(update_fields=["status", "started_at", "completed_at", "error_code", "error_message", "updated_at"])

        from apps.reports.tasks import generate_report_job_task

        transaction.on_commit(lambda: generate_report_job_task.delay(str(job.pk)))
        _safe_write_audit(
            "REPORT_JOB_QUEUED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=requested_by,
            message=f"Report job queued for {job.report_type or 'unknown'}",
        )
        return job

    @classmethod
    def create_manual_job(cls, **kwargs):
        kwargs.setdefault("trigger_source", ReportJobTriggerSource.MANUAL)
        return cls.create_and_enqueue(**kwargs)

    @classmethod
    def create_run_now_job(cls, **kwargs):
        kwargs.setdefault("trigger_source", ReportJobTriggerSource.RUN_NOW)
        return cls.create_and_enqueue(**kwargs)

    @classmethod
    def create_scheduled_job(cls, *, schedule: ReportSchedule, scheduled_for, enqueue: bool = True):
        return cls.create_and_enqueue(
            organization=schedule.organization,
            data_center=schedule.data_center,
            definition=schedule.definition,
            template=schedule.template,
            parameters=schedule.parameters,
            primary_format=schedule.primary_format,
            attachment_formats=schedule.attachment_formats,
            trigger_source=ReportJobTriggerSource.SCHEDULED,
            requested_by=schedule.created_by,
            schedule=schedule,
            scheduled_for=scheduled_for,
            recipients=list(schedule.recipient_entries.filter(is_active=True).values("channel", "recipient_type", "destination", "display_name")),
            enqueue=enqueue,
        )

    @classmethod
    def create_retry_job(cls, *, failed_job: ReportJob, requested_by=None, enqueue: bool = True):
        return cls.create_and_enqueue(
            organization=failed_job.organization,
            data_center=failed_job.data_center,
            definition=failed_job.definition,
            template=failed_job.template,
            parameters=failed_job.parameters_snapshot or failed_job.parameters,
            primary_format=(failed_job.output_config_snapshot or {}).get("primary_format"),
            attachment_formats=(failed_job.output_config_snapshot or {}).get("attachment_formats", []),
            trigger_source=ReportJobTriggerSource.RETRY,
            requested_by=requested_by or failed_job.requested_by,
            schedule=failed_job.schedule,
            scheduled_for=None,
            recipients=failed_job.recipient_snapshot,
            parent_job=failed_job,
            enqueue=enqueue,
        )

    @classmethod
    def cancel_job(cls, job: ReportJob, *, requested_by=None):
        if job.status not in {ReportJobStatus.PENDING, ReportJobStatus.QUEUED, ReportJobStatus.RUNNING, ReportJobStatus.PROCESSING}:
            raise ValueError("Only queued or running jobs can be cancelled safely.")
        job.mark_cancelled(save=True)
        _safe_write_audit(
            "REPORT_JOB_CANCELLED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=requested_by,
            message="Report job cancelled",
        )
        return job
