import logging

from celery import shared_task
from django.db import transaction

from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from .services import deliver_notification, queue_pending_notifications

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue="notifications", max_retries=3)
def send_notification_task(self, notification_id):
    with transaction.atomic():
        notification = (
            Notification.objects.select_related("organization")
            .select_for_update()
            .filter(pk=notification_id)
            .first()
        )
        if not notification:
            logger.warning("Notification not found notification=%s", notification_id)
            return {"status": "missing", "notification_id": str(notification_id)}
        if notification.status == NotificationStatus.SENT:
            logger.info("Notification already sent notification=%s", notification.pk)
            return {"status": "sent", "notification_id": str(notification.pk)}

    try:
        deliver_notification(notification)
        notification.refresh_from_db()
        logger.info("Notification delivered notification=%s channel=%s", notification.pk, notification.channel)
        return {"status": "sent", "notification_id": str(notification.pk)}
    except Exception as exc:
        logger.exception("Notification delivery failed notification=%s", notification_id)
        notification = Notification.objects.filter(pk=notification_id).first()
        if notification:
            notification.status = NotificationStatus.FAILED
            notification.error_message = str(exc)
            notification.save(update_fields=["status", "error_message", "updated_at"])
        if self.request.retries >= self.max_retries:
            raise
        countdown = 10 * (self.request.retries + 1)
        logger.info(
            "Notification retry scheduled notification=%s retry=%s countdown=%s",
            notification_id,
            self.request.retries + 1,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(queue="notifications", max_retries=3)
def deliver_pending_notifications_task(limit=200, older_than_minutes=5, channel=None, ids=None, include_failed=False):
    matched, queued = queue_pending_notifications(
        limit=limit,
        older_than_minutes=older_than_minutes,
        channel=channel,
        ids=ids or None,
        include_failed=include_failed,
    )
    return {
        "matched_count": len(matched),
        "queued_count": len(queued),
        "older_than_minutes": older_than_minutes,
        "channel": channel,
        "include_failed": include_failed,
    }
