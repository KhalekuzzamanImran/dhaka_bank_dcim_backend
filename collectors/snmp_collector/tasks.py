import logging

from celery import shared_task
from django.db import OperationalError, ProgrammingError
from django.conf import settings

from .services import get_enabled_snmp_devices_due, poll_snmp_device

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=2, soft_time_limit=45, time_limit=60)
def poll_snmp_device_task(self, device_id: str):
    outcome = poll_snmp_device(device_id)
    return outcome.__dict__


@shared_task(bind=True, soft_time_limit=30, time_limit=45)
def enqueue_due_snmp_polls(self, limit: int = 200):
    try:
        device_ids = get_enabled_snmp_devices_due(limit=limit)
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("SNMP due poll enqueue skipped because database schema is not ready: %s", exc)
        return {"enqueued": 0, "skipped": True, "reason": "database_schema_not_ready"}
    queue = getattr(settings, "SNMP_CELERY_QUEUE", "snmp")
    for device_id in device_ids:
        poll_snmp_device_task.apply_async(args=[device_id], queue=queue)
    logger.info("Enqueued %s SNMP device poll tasks", len(device_ids))
    return {"enqueued": len(device_ids), "device_ids": device_ids}
