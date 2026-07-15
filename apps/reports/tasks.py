import logging

from celery import shared_task

from .services.generator import generate_report_job
from .services.schedules import claim_due_report_schedules, execute_report_schedule

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue="reports")
def generate_report_job_task(self, report_job_id):
    logger.info("Report generation task started report_job=%s", report_job_id)
    try:
        job = generate_report_job(report_job_id)
        logger.info("Report generation task finished report_job=%s status=%s", report_job_id, getattr(job, "status", None))
        return {
            "report_job_id": str(report_job_id),
            "status": getattr(job, "status", None),
        }
    except Exception as exc:
        logger.exception("Report generation task failed report_job=%s", report_job_id)
        return {
            "report_job_id": str(report_job_id),
            "status": "FAILED",
            "error": str(exc),
        }


@shared_task(bind=True, queue="scheduler")
def enqueue_due_report_schedules_task(self, limit=100):
    logger.info("Checking due report schedules limit=%s", limit)
    claimed = claim_due_report_schedules(limit=limit)
    for entry in claimed:
        deliver_report_schedule_task.delay(entry.schedule_id, entry.window_start, entry.window_end)
    logger.info("Due report schedules queued matched=%s queued=%s", len(claimed), len(claimed))
    return {
        "matched_count": len(claimed),
        "queued_count": len(claimed),
    }


@shared_task(bind=True, queue="reports")
def deliver_report_schedule_task(self, schedule_id, window_start=None, window_end=None):
    logger.info("Delivering scheduled report schedule=%s", schedule_id)
    try:
        schedule = execute_report_schedule(schedule_id, window_start=window_start, window_end=window_end)
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
