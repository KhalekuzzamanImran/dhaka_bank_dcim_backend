import logging

from celery import shared_task
from django.conf import settings

from .retention import cleanup_retention


logger = logging.getLogger(__name__)


@shared_task(bind=True, queue="scheduler")
def cleanup_retention_task(self, dry_run=None, batch_size=None):
    result = cleanup_retention(
        dry_run=dry_run,
        batch_size=batch_size or settings.RETENTION_CLEANUP_BATCH_SIZE,
    )
    logger.info("Retention cleanup completed result=%s", result)
    return result
