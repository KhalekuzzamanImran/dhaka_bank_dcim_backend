from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import Retry
from django.utils import timezone

from apps.notifications.models import Notification, NotificationDelivery, NotificationStatus

from .services import (
    claim_delivery_for_processing,
    claim_notification_for_delivery,
    deliver_notification,
    deliver_notification_delivery,
    deliver_pending_notifications,
    deliver_pending_notification_deliveries,
    queue_pending_notification_deliveries,
    queue_pending_notifications,
    requeue_stale_delivering_notification_deliveries,
    requeue_stale_delivering_notifications,
)

logger = logging.getLogger(__name__)


def _retry_delivery_failure(delivery_id, exc, *, self_task=None):
    delivery = NotificationDelivery.objects.filter(pk=delivery_id).first()
    if delivery:
        delivery.status = NotificationStatus.FAILED
        delivery.failed_at = timezone.now()
        delivery.error_message = str(exc)
        delivery.next_retry_at = timezone.now() + timedelta(minutes=10 * max(1, int(delivery.attempt_count or 1)))
        delivery.save(update_fields=["status", "failed_at", "error_message", "next_retry_at", "updated_at"])
        try:
            from apps.reports.services.deliveries import sync_report_delivery_from_notification_delivery

            sync_report_delivery_from_notification_delivery(delivery, error_message=str(exc))
        except Exception:
            logger.warning("Failed to sync report delivery after notification failure.", exc_info=True)
    if self_task is None:
        return
    if self_task.request.retries >= self_task.max_retries:
        raise exc
    countdown = 10 * (self_task.request.retries + 1)
    logger.info(
        "Notification delivery retry scheduled delivery=%s retry=%s countdown=%s",
        delivery_id,
        self_task.request.retries + 1,
        countdown,
    )
    if getattr(self_task.request, "is_eager", False):
        raise Retry(exc=exc, when=countdown)
    raise self_task.retry(exc=exc, countdown=countdown)


@shared_task(bind=True, queue="notifications", max_retries=3)
def send_notification_delivery_task(self, delivery_id):
    delivery = claim_delivery_for_processing(delivery_id)
    if delivery:
        try:
            deliver_notification_delivery(delivery)
            logger.info(
                "Notification delivery sent delivery=%s channel=%s",
                delivery.pk,
                delivery.channel,
            )
            return {
                "status": "sent",
                "delivery_id": str(delivery.pk),
                "notification_id": str(delivery.notification_id),
                "channel": delivery.channel,
            }
        except Exception as exc:
            logger.exception("Notification delivery failed delivery=%s", delivery_id)
            _retry_delivery_failure(delivery_id, exc, self_task=self)

    current = NotificationDelivery.objects.filter(pk=delivery_id).values_list("status", flat=True).first()
    if current:
        if current == NotificationStatus.SENT:
            return {"status": "sent", "delivery_id": str(delivery_id)}
        if current == NotificationStatus.DELIVERING:
            return {"status": "delivering", "delivery_id": str(delivery_id)}
        if current == NotificationStatus.FAILED:
            return {"status": "failed", "delivery_id": str(delivery_id)}
        return {"status": current.lower(), "delivery_id": str(delivery_id)}

    legacy = claim_notification_for_delivery(delivery_id)
    if legacy:
        try:
            deliver_notification(legacy)
            legacy.refresh_from_db(fields=["status", "sent_at", "error_message"])
            if legacy.status != NotificationStatus.SENT:
                legacy.status = NotificationStatus.SENT
                legacy.sent_at = timezone.now()
                legacy.error_message = ""
                legacy.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
            logger.info("Legacy notification delivered notification=%s channel=%s", legacy.pk, getattr(legacy, "channel", None))
            return {"status": "sent", "notification_id": str(legacy.pk), "channel": getattr(legacy, "channel", None)}
        except Exception as exc:
            logger.exception("Legacy notification delivery failed notification=%s", delivery_id)
            notification = Notification.objects.filter(pk=delivery_id).first()
            if notification:
                notification.status = NotificationStatus.FAILED
                notification.error_message = str(exc)
                notification.save(update_fields=["status", "error_message", "updated_at"])
            if self.request.retries >= self.max_retries:
                raise exc
            countdown = 10 * (self.request.retries + 1)
            if getattr(self.request, "is_eager", False):
                raise Retry(exc=exc, when=countdown)
            raise self.retry(exc=exc, countdown=countdown)

    current_legacy = Notification.objects.filter(pk=delivery_id).values_list("status", flat=True).first()
    if not current_legacy:
        return {"status": "missing", "delivery_id": str(delivery_id)}
    if current_legacy == NotificationStatus.SENT:
        return {"status": "sent", "notification_id": str(delivery_id)}
    if current_legacy == NotificationStatus.DELIVERING:
        return {"status": "delivering", "notification_id": str(delivery_id)}
    return {"status": current_legacy.lower(), "notification_id": str(delivery_id)}


@shared_task(queue="notifications", max_retries=3)
def queue_pending_notification_deliveries_task(limit=200, older_than_minutes=5, channel=None, ids=None, include_failed=False):
    matched, queued = queue_pending_notification_deliveries(
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
def deliver_pending_notification_deliveries_task(limit=200, older_than_minutes=5, channel=None, ids=None, include_failed=False):
    delivered = deliver_pending_notifications(limit=limit)
    return {
        "matched_count": len(delivered),
        "delivered_count": len(delivered),
        "limit": limit,
    }


@shared_task(queue="notifications", max_retries=3)
def requeue_stale_delivering_notification_deliveries_task(limit=100, older_than_minutes=10, channel=None, dry_run=False):
    matched, requeued = requeue_stale_delivering_notification_deliveries(
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


# Backward-compatible legacy task names. The normalized delivery task above
# processes NotificationDelivery rows; this wrapper retains the old command/task
# contract for Notification rows during the migration window.
send_notification_task = send_notification_delivery_task


@shared_task(queue="notifications", max_retries=3)
def deliver_pending_notifications_task(limit=200, older_than_minutes=5, channel=None, ids=None, include_failed=False):
    matched, queued = queue_pending_notifications(
        limit=limit,
        older_than_minutes=older_than_minutes,
        channel=channel,
        ids=ids,
        include_failed=include_failed,
    )
    return {
        "matched_count": len(matched),
        "queued_count": len(queued),
        "limit": limit,
        "older_than_minutes": older_than_minutes,
    }
