import logging

from celery import shared_task
from celery.exceptions import Retry

from .models import ReportDeliveryStatus
from .task_runners import (
    cleanup_expired_report_artifacts_task_impl,
    deliver_report_task_impl,
    dispatch_due_report_schedules_task_impl,
    generate_report_job_task_impl,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue="reports")
def generate_report_job_task(self, report_job_id):
    logger.info("Report generation task started report_job=%s", report_job_id)
    try:
        job = generate_report_job_task_impl(report_job_id)
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
    claimed = dispatch_due_report_schedules_task_impl(limit=limit)
    for job in claimed:
        generate_report_job_task.delay(str(job.pk))
    logger.info("Due report schedules queued matched=%s queued=%s", len(claimed), len(claimed))
    return {
        "matched_count": len(claimed),
        "queued_count": len(claimed),
    }


@shared_task(bind=True, queue="reports")
def deliver_report_schedule_task(self, schedule_id, window_start=None, window_end=None):
    logger.info("Delivering scheduled report schedule=%s", schedule_id)
    try:
        from datetime import timedelta

        from django.utils import timezone

        from apps.reports.domain import ReportJobTriggerSource
        from apps.reports.models import ReportJob
        from apps.reports.services.schedules import execute_report_schedule

        recent_run_now = (
            ReportJob.objects.filter(
                schedule_id=schedule_id,
                trigger_source=ReportJobTriggerSource.RUN_NOW,
                queued_at__gte=timezone.now() - timedelta(minutes=5),
            )
            .order_by("-queued_at")
            .first()
        )
        if recent_run_now:
            logger.info(
                "Skipping legacy scheduled delivery because a run-now job already exists schedule=%s job=%s",
                schedule_id,
                recent_run_now.pk,
            )
            return {
                "schedule_id": str(schedule_id),
                "status": "PENDING",
                "skipped": True,
                "job_id": str(recent_run_now.pk),
            }

        schedule = execute_report_schedule(schedule_id, window_start=window_start, window_end=window_end)
        logger.info("Scheduled report delivery finished schedule=%s status=%s", schedule_id, getattr(schedule, "last_delivery_status", None))
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


@shared_task(bind=True, queue="reports", max_retries=3)
def deliver_report_task(self, delivery_id):
    logger.info("Report delivery task started delivery=%s", delivery_id)
    try:
        delivery = deliver_report_task_impl(delivery_id)
        status = getattr(delivery, "status", None)
        error_message = str(getattr(delivery, "error_message", "") or "")
        transient_error_markers = (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "max retries exceeded",
            "temporary failure",
            "service unavailable",
        )
        if (
            status == ReportDeliveryStatus.FAILED
            and error_message
            and any(marker in error_message.lower() for marker in transient_error_markers)
        ):
            if self.request.retries >= self.max_retries:
                logger.warning(
                    "Transient report delivery failure exhausted retries delivery=%s error=%s",
                    delivery_id,
                    error_message,
                )
                return {"delivery_id": str(delivery_id), "status": status, "error": error_message}

            countdown = 10 * (self.request.retries + 1)
            logger.info(
                "Retrying transient report delivery delivery=%s retry=%s countdown=%s",
                delivery_id,
                self.request.retries + 1,
                countdown,
            )
            if getattr(self.request, "is_eager", False):
                raise Retry(exc=RuntimeError(error_message), when=countdown)
            raise self.retry(exc=RuntimeError(error_message), countdown=countdown)

        return {"delivery_id": str(delivery_id), "status": status}
    except Retry:
        raise
    except Exception as exc:
        logger.exception("Report delivery task failed delivery=%s", delivery_id)
        return {"delivery_id": str(delivery_id), "status": "FAILED", "error": str(exc)}


@shared_task(bind=True, queue="reports")
def cleanup_expired_report_artifacts_task(self, limit=100):
    logger.info("Cleaning up expired report artifacts limit=%s", limit)
    return cleanup_expired_report_artifacts_task_impl(limit=limit)
