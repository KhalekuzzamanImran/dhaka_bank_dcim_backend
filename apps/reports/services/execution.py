from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.common.audit import write_audit
from apps.reports.domain import ReportJobStatus
from apps.reports.models import ReportArtifactStatus, ReportDelivery, ReportDeliveryStatus, ReportJob

from ..delivery.email import send_report_email_delivery
from ..delivery.sms import send_report_sms_delivery
from ..handlers.registry import get_report_handler
from ..renderers.registry import get_report_renderer
from .artifacts import ReportArtifactService

logger = logging.getLogger(__name__)


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write report execution audit log.", exc_info=True)
        return None


@dataclass(frozen=True)
class ExecutionResult:
    job: ReportJob
    artifact_ids: list[str]


class ReportExecutionService:
    @classmethod
    def execute_job(cls, report_job_id: str | int):
        with transaction.atomic():
            job = (
                ReportJob.objects.select_for_update()
                .filter(pk=report_job_id)
                .first()
            )
            if not job:
                raise ValueError(f"Report job {report_job_id} does not exist.")
            if job.status in {ReportJobStatus.CANCELLED}:
                return job
            if job.status not in {ReportJobStatus.QUEUED, ReportJobStatus.PENDING, ReportJobStatus.FAILED}:
                return job
            job.mark_running(save=False)
            job.current_stage = "RUNNING"
            job.save(update_fields=["status", "started_at", "error_code", "error_message", "current_stage", "updated_at"])
            _safe_write_audit(
                "REPORT_GENERATION_STARTED",
                "ReportJob",
                job.pk,
                organization=job.organization,
                actor=job.requested_by,
                message="Report generation started",
            )

        try:
            handler_code = job.definition.code if job.definition_id else job.report_type
            handler = get_report_handler(handler_code)
            if not handler:
                raise ValueError(f"Unsupported report type: {handler_code}")
            template_config = job.template.config if job.template_id and isinstance(job.template.config, dict) else {}
            context = type("ReportHandlerContextProxy", (), {"job": job, "parameters": job.parameters or {}, "template_config": template_config})()
            report_data = handler.build_report_data(context)
            renderer = get_report_renderer((job.output_config_snapshot or {}).get("primary_format") or "CSV")
            if not renderer:
                raise ValueError("RENDERER_NOT_IMPLEMENTED")
            rendered = renderer.render(job=job, report_data=report_data, output_config=job.output_config_snapshot or {})
            artifact = ReportArtifactService.create_primary_artifact(job=job, rendered_artifact=rendered)

            cls._queue_deliveries(job, artifact)
            with transaction.atomic():
                if job.status != ReportJobStatus.CANCELLED:
                    job.status = ReportJobStatus.SUCCEEDED
                    job.completed_at = job.completed_at or timezone.now()
                    job.progress_percent = 100
                    job.current_stage = "COMPLETED"
                    job.error_code = ""
                    job.error_message = ""
                    ReportJob.objects.filter(pk=job.pk).update(
                        status=job.status,
                        completed_at=job.completed_at,
                        progress_percent=job.progress_percent,
                        current_stage=job.current_stage,
                        error_code=job.error_code,
                        error_message=job.error_message,
                        updated_at=timezone.now(),
                    )
                if job.schedule_id:
                    job.schedule.last_job_id = job.pk
                    job.schedule.last_success_at = job.completed_at
                    job.schedule.last_delivery_status = "PENDING"
                    job.schedule.save(update_fields=["last_job", "last_success_at", "last_delivery_status", "updated_at"])
            _safe_write_audit(
                "REPORT_GENERATED",
                "ReportJob",
                job.pk,
                organization=job.organization,
                actor=job.requested_by,
                message="Report generated successfully",
            )
            return job
        except Exception as exc:
            message = str(exc)
            with transaction.atomic():
                if job.status != ReportJobStatus.CANCELLED:
                    job.status = ReportJobStatus.FAILED
                    job.completed_at = job.completed_at or timezone.now()
                    job.error_code = message[:80]
                    job.error_message = message
                    job.current_stage = "FAILED"
                    ReportJob.objects.filter(pk=job.pk).update(
                        status=job.status,
                        completed_at=job.completed_at,
                        error_code=job.error_code,
                        error_message=job.error_message,
                        current_stage=job.current_stage,
                        updated_at=timezone.now(),
                    )
            _safe_write_audit(
                "REPORT_GENERATION_FAILED",
                "ReportJob",
                job.pk,
                organization=job.organization,
                actor=job.requested_by,
                message=message,
            )
            logger.exception("Report execution failed report_job=%s", report_job_id)
            return job

    @classmethod
    def _queue_deliveries(cls, job: ReportJob, artifact):
        recipients = []
        if isinstance(job.recipient_snapshot, list):
            recipients = job.recipient_snapshot
        elif isinstance(job.recipient_snapshot, dict):
            recipients = job.recipient_snapshot.get("email_recipients", []) or job.recipient_snapshot.get("sms_recipients", [])

        if not recipients and job.schedule_id and hasattr(job.schedule, "recipient_entries"):
            recipients = list(job.schedule.recipient_entries.filter(is_active=True).values("channel", "recipient_type", "destination", "display_name"))

        delivery_rows = []
        for recipient in recipients:
            if isinstance(recipient, dict):
                channel = recipient.get("channel", "EMAIL")
                destination = recipient.get("destination")
                recipient_type = recipient.get("recipient_type", "TO")
            else:
                channel = "EMAIL"
                destination = str(recipient)
                recipient_type = "TO"
            if not destination:
                continue
            delivery_rows.append(
                ReportDelivery.objects.create(
                    job=job,
                    schedule=job.schedule if job.schedule_id else None,
                    artifact=artifact,
                    channel=channel,
                    recipient=None,
                    recipient_type=recipient_type,
                    destination_snapshot=destination,
                    attempt_number=1,
                    status=ReportDeliveryStatus.PENDING,
                )
            )

        from apps.reports.tasks import deliver_report_task

        for delivery in delivery_rows:
            transaction.on_commit(lambda delivery_id=delivery.pk: deliver_report_task.delay(str(delivery_id)))
