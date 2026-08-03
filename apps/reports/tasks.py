import logging

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError

from .services.execution import generate_report_job
from .services.deliveries import create_report_deliveries_for_job, execute_report_delivery
from .services.retention import cleanup_expired_report_artifacts
from .services.schedules import claim_due_report_schedules, execute_report_schedule

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue="reports")
def generate_report_job_task(self, report_job_id):
    logger.info("Report generation task started report_job=%s", report_job_id)
    try:
        job = generate_report_job(report_job_id)
        logger.info("Report generation task finished report_job=%s status=%s", report_job_id, getattr(job, "status", None))
        if getattr(job, "status", None) == "FAILED":
            raise RuntimeError(getattr(job, "error_message", "Report generation failed."))
        return {
            "report_job_id": str(report_job_id),
            "status": getattr(job, "status", None),
        }
    except Exception as exc:
        logger.exception("Report generation task failed report_job=%s", report_job_id)
        raise


@shared_task(bind=True, queue="scheduler")
def enqueue_due_report_schedules_task(self, limit=100):
    logger.info("Checking due report schedules limit=%s", limit)
    claimed = claim_due_report_schedules(limit=limit)
    for entry in claimed:
        deliver_report_schedule_task.delay(
            entry.schedule_id,
            entry.window_start,
            entry.window_end,
            "SCHEDULED",
            entry.scheduled_for,
        )
    logger.info("Due report schedules queued matched=%s queued=%s", len(claimed), len(claimed))
    return {
        "matched_count": len(claimed),
        "queued_count": len(claimed),
    }


@shared_task(bind=True, queue="reports")
def deliver_report_schedule_task(self, schedule_id, window_start=None, window_end=None, trigger_source="SCHEDULED", scheduled_for=None):
    logger.info("Delivering scheduled report schedule=%s", schedule_id)
    try:
        schedule = execute_report_schedule(
            schedule_id,
            window_start=window_start,
            window_end=window_end,
            scheduled_for=scheduled_for,
            trigger_source=trigger_source,
        )
        logger.info(
            "Scheduled report delivery finished schedule=%s status=%s",
            schedule_id,
            getattr(schedule, "last_delivery_status", None),
        )
        return {
            "schedule_id": str(schedule_id),
            "status": getattr(schedule, "last_delivery_status", None),
        }
    except Exception as exc:
        logger.exception("Scheduled report delivery failed schedule=%s", schedule_id)
        return {
            "schedule_id": str(schedule_id),
            "status": "FAILED",
            "error": str(exc),
        }


@shared_task(bind=True, queue="reports")
def cleanup_expired_report_artifacts_task(self, dry_run=False, batch_size=None, now=None):
    logger.info(
        "Cleaning up expired report artifacts dry_run=%s batch_size=%s now=%s",
        dry_run,
        batch_size,
        now,
    )
    try:
        result = cleanup_expired_report_artifacts(
            now=now,
            batch_size=batch_size or getattr(settings, "REPORT_ARTIFACT_CLEANUP_BATCH_SIZE", 200),
            dry_run=dry_run,
        )
        logger.info(
            "Expired report artifact cleanup finished examined=%s deleted=%s failed=%s skipped=%s disabled=%s",
            result.get("examined"),
            result.get("deleted"),
            result.get("failed"),
            result.get("skipped"),
            result.get("disabled"),
        )
        return result
    except Exception:
        logger.exception("Expired report artifact cleanup failed")
        raise


@shared_task(bind=True, queue="reports", max_retries=3)
def queue_report_deliveries_for_job_task(self, report_job_id):
    logger.info("Queueing report deliveries report_job=%s", report_job_id)
    try:
        from .models import ReportJob

        job = ReportJob.objects.select_related("organization", "data_center", "template", "definition", "schedule").filter(pk=report_job_id).first()
        if not job:
            return {"report_job_id": str(report_job_id), "status": "missing"}
        result = create_report_deliveries_for_job(job=job)
        logger.info(
            "Report deliveries created report_job=%s created=%s queued=%s skipped=%s",
            report_job_id,
            result.get("created_count"),
            result.get("queued_count"),
            result.get("skipped_count"),
        )
        return {
            "report_job_id": str(report_job_id),
            "status": "queued",
            "created_count": result.get("created_count"),
            "queued_count": result.get("queued_count"),
            "skipped_count": result.get("skipped_count"),
        }
    except Exception:
        logger.exception("Report delivery queueing failed report_job=%s", report_job_id)
        raise


@shared_task(bind=True, queue="reports", max_retries=3)
def execute_report_delivery_task(self, delivery_id):
    logger.info("Executing report delivery delivery=%s", delivery_id)
    try:
        delivery = execute_report_delivery(delivery_id=delivery_id)
        if not delivery:
            return {"delivery_id": str(delivery_id), "status": "missing"}
        logger.info("Report delivery finished delivery=%s status=%s", delivery_id, getattr(delivery, "status", None))
        return {
            "delivery_id": str(delivery_id),
            "status": getattr(delivery, "status", None),
            "channel": getattr(delivery, "channel", None),
        }
    except Exception as exc:
        logger.exception("Report delivery failed delivery=%s", delivery_id)
        if isinstance(exc, (ValidationError, ValueError)):
            raise
        if self.request.retries >= self.max_retries:
            raise
        countdown = 10 * (self.request.retries + 1)
        raise self.retry(exc=exc, countdown=countdown)
