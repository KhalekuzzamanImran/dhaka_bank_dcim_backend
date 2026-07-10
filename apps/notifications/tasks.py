import logging

from celery import shared_task

from apps.notifications.models import Notification, NotificationStatus
from .services import (
    claim_notification_for_delivery,
    deliver_notification,
    queue_pending_notifications,
    requeue_stale_delivering_notifications,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, queue="notifications", max_retries=3)
def send_notification_task(self, notification_id):
    notification = claim_notification_for_delivery(notification_id)
    if not notification:
        current = Notification.objects.filter(pk=notification_id).values_list("status", flat=True).first()
        if not current:
            return {"status": "missing", "notification_id": str(notification_id)}
        if current == NotificationStatus.SENT:
            return {"status": "sent", "notification_id": str(notification_id)}
        if current == NotificationStatus.DELIVERING:
            return {"status": "delivering", "notification_id": str(notification_id)}
        return {"status": current.lower(), "notification_id": str(notification_id)}

    try:
        deliver_notification(notification)
        from django.utils import timezone

        notification.status = NotificationStatus.SENT
        notification.sent_at = timezone.now()
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
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


@shared_task(queue="notifications", max_retries=3)
def requeue_stale_delivering_notifications_task(limit=100, older_than_minutes=10, channel=None, dry_run=False):
    matched, requeued = requeue_stale_delivering_notifications(
        older_than_minutes=older_than_minutes,
        limit=limit,
        channel=channel,
        dry_run=dry_run,
    )
    return {
        "matched_count": len(matched),
        "requeued_count": len(requeued),
        "older_than_minutes": older_than_minutes,
        "channel": channel,
        "dry_run": dry_run,
    }
