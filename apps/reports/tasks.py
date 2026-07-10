import logging

from celery import shared_task

from .services.generator import generate_report_job

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
