import logging
import random

from celery import shared_task
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from apps.devices.models import DevicePollingConfig, ProtocolType
from collectors.modbus_collector.tasks import poll_modbus_device_task
from collectors.snmp_collector.tasks import poll_snmp_device_task

logger = logging.getLogger(__name__)


def calculate_next_poll(interval_seconds):
    interval_seconds = int(interval_seconds or 60)
    jitter = random.randint(0, max(1, min(10, interval_seconds // 5)))
    return timezone.now() + timezone.timedelta(seconds=interval_seconds + jitter)


def get_queue(protocol, priority):
    priority = (priority or "NORMAL").lower()
    if protocol == ProtocolType.SNMP:
        return {"high": "snmp_high", "normal": "snmp_normal", "low": "snmp_low"}.get(priority, "snmp_normal")
    if protocol == ProtocolType.MODBUS_TCP:
        return {"high": "modbus_high", "normal": "modbus_normal", "low": "modbus_low"}.get(priority, "modbus_normal")
    return "default"


@shared_task(queue="scheduler", soft_time_limit=30, time_limit=45)
def enqueue_due_polls(limit=200):
    """Enqueue due polling jobs.

    This task can start before migrations are applied when docker compose starts all
    services together. In that case, do not crash-loop workers; skip until the
    database schema exists. After `python manage.py migrate`, the next Beat tick
    will enqueue normally.
    """
    now = timezone.now()

    try:
        configs = (
            DevicePollingConfig.objects.select_related("device", "polling_profile")
            .filter(is_enabled=True, device__is_active=True, next_poll_at__lte=now, polling_profile__is_active=True)
            .order_by("next_poll_at")[:limit]
        )

        enqueued = 0
        for config in configs:
            protocol = config.polling_profile.protocol
            priority = getattr(config.polling_profile, "priority", "NORMAL")
            queue = get_queue(protocol, priority)
            if protocol == ProtocolType.SNMP:
                poll_snmp_device_task.apply_async(args=[str(config.device_id)], queue=queue)
            elif protocol == ProtocolType.MODBUS_TCP:
                poll_modbus_device_task.apply_async(args=[str(config.device_id)], queue=queue)
            else:
                continue
            config.next_poll_at = calculate_next_poll(config.polling_profile.interval_seconds)
            config.save(update_fields=["next_poll_at", "updated_at"])
            enqueued += 1

    except (ProgrammingError, OperationalError) as exc:
        logger.warning("Polling scheduler skipped because database schema is not ready: %s", exc)
        return {"enqueued": 0, "skipped": True, "reason": "database_schema_not_ready", "time": now.isoformat()}

    logger.info("Enqueued due poll tasks count=%s", enqueued)
    return {"enqueued": enqueued, "time": now.isoformat()}
