from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .email import send_email_notification
from .sms import send_sms_notification
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

logger = logging.getLogger(__name__)


def queue_notification_delivery(notification: Notification):
    if notification.status == NotificationStatus.SENT:
        logger.info("Skipping queue for already sent notification=%s", notification.pk)
        return notification
    from apps.notifications.tasks import send_notification_task

    logger.info(
        "Notification queued notification=%s channel=%s status=%s",
        notification.pk,
        notification.channel,
        notification.status,
    )
    send_notification_task.delay(str(notification.pk))


def enqueue_delivery():
    from apps.notifications.tasks import deliver_pending_notifications_task

    deliver_pending_notifications_task.delay()


def _notification_requeue_queryset(
    limit: int = 200,
    older_than_minutes: int = 5,
    channel: str | list[str] | tuple[str, ...] | None = None,
    ids: list[str] | None = None,
    include_failed: bool = False,
):
    statuses = [NotificationStatus.PENDING]
    if include_failed:
        statuses.append(NotificationStatus.FAILED)

    qs = Notification.objects.filter(status__in=statuses)
    if older_than_minutes is not None:
        cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
        qs = qs.filter(created_at__lte=cutoff)
    if channel:
        if isinstance(channel, (list, tuple, set)):
            qs = qs.filter(channel__in=list(channel))
        else:
            qs = qs.filter(channel=channel)
    if ids:
        qs = qs.filter(id__in=ids)
    return qs.order_by("created_at", "id")[:limit]


def queue_pending_notifications(
    limit: int = 200,
    older_than_minutes: int = 5,
    channel: str | list[str] | tuple[str, ...] | None = None,
    ids: list[str] | None = None,
    include_failed: bool = False,
):
    pending_qs = _notification_requeue_queryset(
        limit=limit,
        older_than_minutes=older_than_minutes,
        channel=channel,
        ids=ids,
        include_failed=include_failed,
    )
    matched = list(pending_qs)
    queued = []
    for notification in matched:
        try:
            queue_notification_delivery(notification)
            queued.append(notification)
        except Exception:
            logger.exception("Failed to queue pending notification=%s", notification.pk)
    logger.info(
        "Pending notifications queued matched=%s queued=%s channel=%s older_than_minutes=%s include_failed=%s",
        len(matched),
        len(queued),
        channel,
        older_than_minutes,
        include_failed,
    )
    return matched, queued


def deliver_notification(notification: Notification):
    if notification.status == NotificationStatus.SENT:
        return notification

    if notification.channel == NotificationChannel.WEB:
        notification.status = NotificationStatus.SENT
        notification.sent_at = timezone.now()
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        logger.info("Web notification marked as sent notification=%s", notification.pk)
        return notification

    if notification.channel == NotificationChannel.EMAIL:
        send_email_notification(notification)
    elif notification.channel == NotificationChannel.SMS:
        send_sms_notification(notification)
    else:
        notification.status = NotificationStatus.SENT
        notification.sent_at = timezone.now()
        notification.save(update_fields=["status", "sent_at", "updated_at"])
        return notification

    notification.status = NotificationStatus.SENT
    notification.sent_at = timezone.now()
    notification.error_message = ""
    notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
    return notification


def deliver_pending_notifications(limit: int = 200):
    delivered = []
    with transaction.atomic():
        pending = list(
            Notification.objects.select_for_update(skip_locked=True)
            .select_related("organization")
            .filter(status=NotificationStatus.PENDING)
            .order_by("created_at")[:limit]
        )
        for notification in pending:
            try:
                delivered.append(deliver_notification(notification))
            except Exception as exc:
                logger.exception("Notification delivery failed notification=%s", notification.pk)
                notification.status = NotificationStatus.FAILED
                notification.error_message = str(exc)
                notification.save(update_fields=["status", "error_message", "updated_at"])
                delivered.append(notification)
    return delivered
