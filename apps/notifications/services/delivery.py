from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.db import models, transaction
from django.utils import timezone

from apps.notifications.models import Notification, NotificationChannel, NotificationDelivery, NotificationStatus

from .email import send_email_notification
from .sms import send_sms_notification

logger = logging.getLogger(__name__)


def _now():
    return timezone.now()


def _is_delivery_row(target) -> bool:
    return isinstance(target, NotificationDelivery) or hasattr(target, "recipient_address")


def _notification_for_delivery(target):
    return getattr(target, "notification", None) or target


def _legacy_notification(channel_notification: Notification | None):
    if channel_notification is None:
        return None
    if isinstance(channel_notification, NotificationDelivery):
        return None
    if not hasattr(channel_notification, "channel"):
        return None
    return channel_notification


def _legacy_channel_status_queryset(older_than_minutes: int = 10, channel: str | list[str] | tuple[str, ...] | None = None):
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    qs = Notification.objects.filter(status=NotificationStatus.DELIVERING, updated_at__lte=cutoff, deliveries__isnull=True)
    if channel:
        if isinstance(channel, (list, tuple, set)):
            qs = qs.filter(channel__in=list(channel))
        else:
            qs = qs.filter(channel=channel)
    return qs


def _resolve_delivery_address(delivery: NotificationDelivery):
    notification = delivery.notification
    recipient = getattr(notification, "recipient", None)

    if delivery.channel == NotificationChannel.EMAIL:
        if delivery.recipient_address:
            return delivery.recipient_address
        return getattr(recipient, "email", None)

    if delivery.channel == NotificationChannel.SMS:
        if delivery.recipient_address:
            return delivery.recipient_address
        for field in ("phone", "mobile", "phone_number", "msisdn"):
            value = getattr(recipient, field, None) if recipient else None
            if value:
                return str(value).strip()
        metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
        return metadata.get("phone") or metadata.get("mobile") or metadata.get("msisdn")

    return delivery.recipient_address


def _mark_delivery_sent(delivery: NotificationDelivery, provider_response=None):
    now = _now()
    delivery.status = NotificationStatus.SENT
    delivery.queued_at = delivery.queued_at or now
    delivery.delivering_at = delivery.delivering_at or now
    delivery.sent_at = now
    delivery.failed_at = None
    delivery.next_retry_at = None
    delivery.error_message = ""
    if provider_response is not None:
        delivery.provider_response = provider_response if isinstance(provider_response, dict) else {"response": provider_response}
        if isinstance(provider_response, dict):
            provider_message_id = provider_response.get("provider_message_id") or provider_response.get("sms_csms_id") or provider_response.get("sms_ref_no")
            if provider_message_id:
                delivery.provider_message_id = str(provider_message_id)
    delivery.save(
        update_fields=[
            "status",
            "queued_at",
            "delivering_at",
            "sent_at",
            "failed_at",
            "next_retry_at",
            "error_message",
            "provider_response",
            "provider_message_id",
            "updated_at",
        ]
    )
    _sync_report_delivery_from_notification_delivery(delivery, provider_response=provider_response)
    return delivery


def _mark_delivery_failed(delivery: NotificationDelivery, exc: Exception):
    now = _now()
    delivery.status = NotificationStatus.FAILED
    delivery.failed_at = now
    delivery.error_message = str(exc)
    if delivery.attempt_count < delivery.max_attempts:
        delivery.next_retry_at = now + timedelta(minutes=max(1, delivery.attempt_count or 1) * 10)
    delivery.save(
        update_fields=[
            "status",
            "failed_at",
            "error_message",
            "next_retry_at",
            "updated_at",
        ]
    )
    _sync_report_delivery_from_notification_delivery(delivery, error_message=str(exc))
    return delivery


def _sync_report_delivery_from_notification_delivery(delivery: NotificationDelivery, *, provider_response=None, error_message: str | None = None):
    report_delivery = getattr(delivery, "report_delivery", None)
    if not report_delivery:
        return None
    try:
        from apps.reports.services.deliveries import sync_report_delivery_from_notification_delivery

        return sync_report_delivery_from_notification_delivery(
            delivery,
            provider_response=provider_response,
            error_message=error_message,
        )
    except Exception:
        logger.exception(
            "Failed to sync report delivery state from notification delivery=%s",
            getattr(delivery, "pk", None),
        )
        return None


def claim_delivery_for_processing(delivery_id: str | int):
    """Atomically move a notification delivery into DELIVERING."""

    with transaction.atomic():
        delivery = (
            NotificationDelivery.objects.select_for_update()
            .select_related("notification")
            .filter(pk=delivery_id)
            .first()
        )
        if not delivery:
            return None
        if delivery.status in {NotificationStatus.SENT, NotificationStatus.DELIVERING}:
            return None
        delivery.status = NotificationStatus.DELIVERING
        delivery.delivering_at = _now()
        delivery.attempt_count = (delivery.attempt_count or 0) + 1
        delivery.error_message = ""
        delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "status",
                "delivering_at",
                "attempt_count",
                "error_message",
                "next_retry_at",
                "updated_at",
            ]
        )
        return delivery


def claim_notification_for_delivery(notification_id: str | int):
    """Backward-compatible wrapper for legacy notification rows."""

    delivery = claim_delivery_for_processing(notification_id)
    if delivery:
        return delivery

    with transaction.atomic():
        notification = (
            Notification.objects.select_for_update()
            .filter(pk=notification_id, deliveries__isnull=True)
            .first()
        )
        if not notification:
            return None
        if notification.status in {NotificationStatus.SENT, NotificationStatus.DELIVERING}:
            return None
        notification.status = NotificationStatus.DELIVERING
        notification.sent_at = None
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        return notification


def queue_notification_delivery(target):
    """Queue a delivery row for asynchronous processing.

    Legacy Notification rows are still accepted so older test paths and commands
    keep working during the migration window.
    """

    if isinstance(target, NotificationDelivery):
        if target.channel == NotificationChannel.WEB:
            return _mark_delivery_sent(target)
        if target.status in {NotificationStatus.SENT, NotificationStatus.DELIVERING}:
            logger.info("Skipping queue for already claimed delivery=%s status=%s", target.pk, target.status)
            return target
        from apps.notifications.tasks import send_notification_task

        logger.info(
            "Notification delivery queued delivery=%s channel=%s status=%s",
            target.pk,
            target.channel,
            target.status,
        )

        def _dispatch():
            send_notification_task.delay(str(target.pk))

        transaction.on_commit(_dispatch)
        return target

    legacy = _legacy_notification(target)
    if legacy is None:
        return target
    if legacy.channel == NotificationChannel.WEB:
        legacy.status = NotificationStatus.SENT
        legacy.sent_at = _now()
        legacy.error_message = ""
        legacy.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        return legacy
    if legacy.status in {NotificationStatus.SENT, NotificationStatus.DELIVERING}:
        return legacy
    from apps.notifications.tasks import send_notification_task

    def _dispatch_legacy():
        send_notification_task.delay(str(legacy.pk))

    transaction.on_commit(_dispatch_legacy)
    return legacy


def _stale_delivering_delivery_queryset(older_than_minutes: int = 10, channel: str | list[str] | tuple[str, ...] | None = None):
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    qs = NotificationDelivery.objects.filter(status=NotificationStatus.DELIVERING, updated_at__lte=cutoff)
    if channel:
        if isinstance(channel, (list, tuple, set)):
            qs = qs.filter(channel__in=list(channel))
        else:
            qs = qs.filter(channel=channel)
    return qs


def requeue_stale_delivering_notification_deliveries(
    older_than_minutes: int = 10,
    limit: int = 100,
    channel: str | list[str] | tuple[str, ...] | None = None,
    dry_run: bool = False,
):
    qs = _stale_delivering_delivery_queryset(older_than_minutes=older_than_minutes, channel=channel).order_by("updated_at", "id")
    matched = list(qs[:limit])
    if dry_run:
        return matched, []

    requeued = []
    now = _now()
    with transaction.atomic():
        for delivery in NotificationDelivery.objects.select_for_update().filter(pk__in=[row.pk for row in matched]):
            if delivery.status != NotificationStatus.DELIVERING:
                continue
            if delivery.updated_at and delivery.updated_at > now - timedelta(minutes=older_than_minutes):
                continue
            delivery.status = NotificationStatus.PENDING
            delivery.error_message = "Requeued after stale DELIVERING timeout"
            delivery.next_retry_at = now
            delivery.save(update_fields=["status", "error_message", "next_retry_at", "updated_at"])
            requeued.append(delivery)

    return matched, requeued


def requeue_stale_delivering_notifications(
    older_than_minutes: int = 10,
    limit: int = 100,
    channel: str | list[str] | tuple[str, ...] | None = None,
    dry_run: bool = False,
):
    """Backward-compatible wrapper that also handles legacy notification rows."""

    deliveries, requeued = requeue_stale_delivering_notification_deliveries(
        older_than_minutes=older_than_minutes,
        limit=limit,
        channel=channel,
        dry_run=dry_run,
    )
    if deliveries:
        return deliveries, requeued
    legacy_qs = _legacy_channel_status_queryset(older_than_minutes=older_than_minutes, channel=channel).order_by("updated_at", "id")
    matched = list(legacy_qs[:limit])
    if dry_run:
        return matched, []

    requeued_legacy = []
    now = _now()
    with transaction.atomic():
        for notification in Notification.objects.select_for_update().filter(pk__in=[row.pk for row in matched]):
            if notification.status != NotificationStatus.DELIVERING:
                continue
            if notification.updated_at and notification.updated_at > now - timedelta(minutes=older_than_minutes):
                continue
            notification.status = NotificationStatus.PENDING
            notification.error_message = "Requeued after stale DELIVERING timeout"
            notification.save(update_fields=["status", "error_message", "updated_at"])
            requeued_legacy.append(notification)
    return matched, requeued_legacy


def enqueue_delivery():
    from apps.notifications.tasks import deliver_pending_notification_deliveries_task

    deliver_pending_notification_deliveries_task.delay()


def _pending_delivery_queryset(
    limit: int = 200,
    older_than_minutes: int = 5,
    channel: str | list[str] | tuple[str, ...] | None = None,
    ids: list[str] | None = None,
    include_failed: bool = False,
):
    statuses = [NotificationStatus.PENDING]
    if include_failed:
        statuses.append(NotificationStatus.FAILED)

    qs = NotificationDelivery.objects.filter(status__in=statuses)
    if older_than_minutes is not None:
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        qs = qs.filter(created_at__lte=cutoff)
    if channel:
        if isinstance(channel, (list, tuple, set)):
            qs = qs.filter(channel__in=list(channel))
        else:
            qs = qs.filter(channel=channel)
    if ids:
        qs = qs.filter(id__in=ids)
    if include_failed:
        qs = qs.filter(models.Q(next_retry_at__isnull=True) | models.Q(next_retry_at__lte=_now()))
    return qs.order_by("created_at", "id")[:limit]


def _legacy_pending_queryset(
    limit: int = 200,
    older_than_minutes: int = 5,
    channel: str | list[str] | tuple[str, ...] | None = None,
    ids: list[str] | None = None,
    include_failed: bool = False,
):
    statuses = [NotificationStatus.PENDING]
    if include_failed:
        statuses.append(NotificationStatus.FAILED)

    qs = Notification.objects.filter(status__in=statuses, deliveries__isnull=True)
    if older_than_minutes is not None:
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        qs = qs.filter(created_at__lte=cutoff)
    if channel:
        if isinstance(channel, (list, tuple, set)):
            qs = qs.filter(channel__in=list(channel))
        else:
            qs = qs.filter(channel=channel)
    if ids:
        qs = qs.filter(id__in=ids)
    return qs.order_by("created_at", "id")[:limit]


def queue_pending_notification_deliveries(
    limit: int = 200,
    older_than_minutes: int = 5,
    channel: str | list[str] | tuple[str, ...] | None = None,
    ids: list[str] | None = None,
    include_failed: bool = False,
):
    pending_qs = _pending_delivery_queryset(
        limit=limit,
        older_than_minutes=older_than_minutes,
        channel=channel,
        ids=ids,
        include_failed=include_failed,
    )
    matched = list(pending_qs)
    queued = []
    for delivery in matched:
        try:
            queue_notification_delivery(delivery)
            queued.append(delivery)
        except Exception:
            logger.exception("Failed to queue pending delivery=%s", delivery.pk)
    return matched, queued


def queue_pending_notifications(
    limit: int = 200,
    older_than_minutes: int = 5,
    channel: str | list[str] | tuple[str, ...] | None = None,
    ids: list[str] | None = None,
    include_failed: bool = False,
):
    """Backward-compatible wrapper that also handles legacy notification rows."""

    matched, queued = queue_pending_notification_deliveries(
        limit=limit,
        older_than_minutes=older_than_minutes,
        channel=channel,
        ids=ids,
        include_failed=include_failed,
    )
    if matched:
        return matched, queued

    pending_qs = _legacy_pending_queryset(
        limit=limit,
        older_than_minutes=older_than_minutes,
        channel=channel,
        ids=ids,
        include_failed=include_failed,
    )
    legacy_matched = list(pending_qs)
    queued_legacy = []
    for notification in legacy_matched:
        try:
            queue_notification_delivery(notification)
            queued_legacy.append(notification)
        except Exception:
            logger.exception("Failed to queue legacy notification=%s", notification.pk)
    return legacy_matched, queued_legacy


def deliver_notification_delivery(delivery: NotificationDelivery, *, email_subject=None, email_body=None, email_attachments=None, sms_message=None):
    notification = delivery.notification
    if delivery.channel == NotificationChannel.WEB:
        return _mark_delivery_sent(delivery)

    if delivery.channel == NotificationChannel.EMAIL:
        provider_response = send_email_notification(
            delivery,
            subject=email_subject,
            body=email_body,
            attachments=email_attachments,
        )
        return _mark_delivery_sent(delivery, provider_response=provider_response)

    if delivery.channel == NotificationChannel.SMS:
        provider_response = send_sms_notification(delivery, message=sms_message)
        return _mark_delivery_sent(delivery, provider_response=provider_response)

    if delivery.channel == NotificationChannel.WEBHOOK:
        metadata = delivery.metadata if isinstance(delivery.metadata, dict) else {}
        notification_metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
        webhook_url = (
            delivery.recipient_address
            or metadata.get("webhook_url")
            or metadata.get("url")
            or notification_metadata.get("webhook_url")
            or notification_metadata.get("url")
        )
        if not webhook_url:
            raise ValueError("Webhook URL is missing for webhook delivery")
        timeout = float(metadata.get("timeout_seconds") or notification_metadata.get("timeout_seconds") or 10)
        payload = {
            "notification_id": str(notification.pk),
            "subject": notification.subject,
            "message": notification.message,
            "status": delivery.status,
            "metadata": {
                **notification_metadata,
                **metadata,
            },
        }
        response = requests.post(webhook_url, json=payload, timeout=timeout)
        response.raise_for_status()
        provider_response = {"backend": "webhook", "status_code": response.status_code, "url": webhook_url}
        return _mark_delivery_sent(delivery, provider_response=provider_response)

    raise ValueError(f"Unsupported notification channel: {delivery.channel}")


def deliver_notification(notification: Notification):
    """Backward-compatible wrapper for legacy notification rows."""

    if isinstance(notification, NotificationDelivery):
        return deliver_notification_delivery(notification)

    channel = getattr(notification, "channel", None)
    if channel == NotificationChannel.WEB:
        notification.status = NotificationStatus.SENT
        notification.sent_at = _now()
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        return notification

    if channel == NotificationChannel.EMAIL:
        provider_response = send_email_notification(notification)
        notification.status = NotificationStatus.SENT
        notification.sent_at = _now()
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        return provider_response

    if channel == NotificationChannel.SMS:
        provider_response = send_sms_notification(notification)
        notification.status = NotificationStatus.SENT
        notification.sent_at = _now()
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        return provider_response

    if channel == NotificationChannel.WEBHOOK:
        metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
        webhook_url = metadata.get("webhook_url") or metadata.get("url")
        if not webhook_url:
            raise ValueError("Webhook URL is missing for webhook notification")
        timeout = float(metadata.get("timeout_seconds") or 10)
        payload = {
            "notification_id": str(notification.pk),
            "subject": notification.subject,
            "message": notification.message,
            "status": notification.status,
            "metadata": metadata,
        }
        response = requests.post(webhook_url, json=payload, timeout=timeout)
        response.raise_for_status()
        notification.status = NotificationStatus.SENT
        notification.sent_at = _now()
        notification.error_message = ""
        notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
        return {"backend": "webhook", "status_code": response.status_code, "url": webhook_url}

    notification.status = NotificationStatus.SENT
    notification.sent_at = _now()
    notification.error_message = ""
    notification.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
    return notification


def deliver_pending_notification_deliveries(limit: int = 200):
    delivered = []
    pending_ids = list(
        NotificationDelivery.objects.filter(status=NotificationStatus.PENDING)
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    for delivery_id in pending_ids:
        delivery = claim_delivery_for_processing(delivery_id)
        if not delivery:
            continue
        try:
            deliver_notification_delivery(delivery)
            delivered.append(delivery)
        except Exception as exc:
            logger.exception("Notification delivery failed delivery=%s", delivery_id)
            delivery = NotificationDelivery.objects.filter(pk=delivery_id).first()
            if delivery:
                _mark_delivery_failed(delivery, exc)
                delivered.append(delivery)
    return delivered


def deliver_pending_notifications(limit: int = 200):
    """Backward-compatible wrapper that also handles legacy notification rows."""

    deliveries = deliver_pending_notification_deliveries(limit=limit)
    if deliveries:
        return deliveries

    delivered = []
    pending_ids = list(
        Notification.objects.filter(status=NotificationStatus.PENDING, deliveries__isnull=True)
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    for notification_id in pending_ids:
        notification = claim_notification_for_delivery(notification_id)
        if not notification:
            continue
        try:
            deliver_notification(notification)
            delivered.append(notification)
        except Exception as exc:
            logger.exception("Notification delivery failed notification=%s", notification.pk)
            notification = Notification.objects.filter(pk=notification_id).first()
            if notification:
                notification.status = NotificationStatus.FAILED
                notification.error_message = str(exc)
                notification.save(update_fields=["status", "error_message", "updated_at"])
                delivered.append(notification)
    return delivered
