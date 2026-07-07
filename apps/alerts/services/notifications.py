from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from apps.access_control.models import UserResourceAccess
from apps.common.access import get_access_scope, get_effective_permission_codes
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

logger = logging.getLogger(__name__)

_ALERT_PERMISSION_CODES = {"alert.view", "alert.acknowledge"}


def _has_alert_scope(user, alert) -> bool:
    scope = get_access_scope(user)
    if scope.get("global_access"):
        return True
    if alert.organization_id and alert.organization_id in scope["organization_ids"]:
        return True
    if alert.data_center_id and alert.data_center_id in scope["data_center_ids"]:
        return True
    if alert.device_id and alert.device_id in scope["device_ids"]:
        return True
    return False


def _recipient_phone(user):
    for field in ("phone", "mobile", "phone_number", "msisdn"):
        value = getattr(user, field, None)
        if value:
            return str(value).strip()
    return None


def get_alert_recipients(alert):
    from apps.accounts.models import User

    user_ids = set(
        UserResourceAccess.objects.filter(is_active=True).values_list("user_id", flat=True).distinct()
    )
    user_ids.update(User.objects.filter(is_superuser=True, is_active=True).values_list("id", flat=True))
    recipients = []

    for user in User.objects.filter(id__in=user_ids, is_active=True):
        if not _has_alert_scope(user, alert):
            continue
        perms = get_effective_permission_codes(user)
        if perms.intersection(_ALERT_PERMISSION_CODES) or user.is_staff or user.is_superuser:
            recipients.append(user)
    return recipients


def _channels_for_open(severity: str) -> list[str]:
    severity = (severity or "").upper()
    if severity == "INFO":
        return [NotificationChannel.WEB]
    if severity == "WARNING":
        return [NotificationChannel.WEB, NotificationChannel.EMAIL]
    if severity in {"CRITICAL", "EMERGENCY"}:
        return [NotificationChannel.WEB, NotificationChannel.EMAIL, NotificationChannel.SMS]
    return [NotificationChannel.WEB]


def _channels_for_resolved(severity: str) -> list[str]:
    severity = (severity or "").upper()
    if severity in {"CRITICAL", "EMERGENCY"}:
        return [NotificationChannel.WEB, NotificationChannel.EMAIL]
    return [NotificationChannel.WEB]


def _alert_message(alert):
    value = alert.value_text
    if value is None and alert.value_integer is not None:
        value = str(alert.value_integer)
    if value is None and alert.value_float is not None:
        value = str(alert.value_float)
    if value is None and alert.value_boolean is not None:
        value = str(alert.value_boolean)
    metric = getattr(alert, "metric", None)
    metric_code = metric.code if metric and getattr(alert, "metric_id", None) else ""
    return f"{alert.device.name} {metric_code} {alert.status}: {alert.message}".strip()


def _dedupe_key(alert, user, channel: str, action: str) -> str:
    return f"alert:{alert.pk}:{action}:{channel}:{user.pk}"


def _create_notification(alert, user, channel, subject, message, *, action: str):
    if channel == NotificationChannel.EMAIL and not getattr(user, "email", None):
        logger.info("Skipping EMAIL notification due to missing email user=%s alert=%s", user.pk, alert.pk)
        return None
    if channel == NotificationChannel.SMS and not _recipient_phone(user):
        logger.info("Skipping SMS notification due to missing phone user=%s alert=%s", user.pk, alert.pk)
        return None
    dedupe_key = _dedupe_key(alert, user, channel, action)

    with transaction.atomic():
        existing = Notification.objects.filter(dedupe_key=dedupe_key).first()
        if existing:
            logger.info(
                "Skipping duplicate notification by dedupe key user=%s alert=%s channel=%s dedupe_key=%s",
                user.pk,
                alert.pk,
                channel,
                dedupe_key,
            )
            return None

        legacy = Notification.objects.filter(
            recipient=user,
            channel=channel,
            metadata__alert_event_id=str(alert.pk),
            metadata__action=action,
        ).order_by("-created_at", "-updated_at").first()
        if legacy:
            if not legacy.dedupe_key:
                legacy.dedupe_key = dedupe_key
                try:
                    legacy.save(update_fields=["dedupe_key", "updated_at"])
                except IntegrityError:
                    logger.info(
                        "Legacy notification already claimed by dedupe key user=%s alert=%s channel=%s dedupe_key=%s",
                        user.pk,
                        alert.pk,
                        channel,
                        dedupe_key,
                    )
                return None
            return None

        try:
            notification = Notification.objects.create(
                organization=alert.organization,
                recipient=user,
                channel=channel,
                dedupe_key=dedupe_key,
                subject=subject,
                message=message,
                status=NotificationStatus.PENDING,
                metadata={
                    "alert_event_id": str(alert.pk),
                    "alert_status": alert.status,
                    "severity": alert.severity,
                    "device_id": str(alert.device_id) if alert.device_id else None,
                    "metric_id": str(alert.metric_id) if alert.metric_id else None,
                    "action": action,
                },
            )
        except IntegrityError:
            notification = Notification.objects.filter(dedupe_key=dedupe_key).first()
            if not notification:
                raise
            logger.info(
                "Duplicate notification raced into existence user=%s alert=%s channel=%s dedupe_key=%s",
                user.pk,
                alert.pk,
                channel,
                dedupe_key,
            )
            return None

    logger.info(
        "Notification created notification=%s alert=%s user=%s channel=%s dedupe_key=%s",
        notification.pk,
        alert.pk,
        user.pk,
        channel,
        dedupe_key,
    )
    return notification


def _queue_delivery(notification):
    if not notification:
        return None
    from apps.notifications.services import queue_notification_delivery

    queue_notification_delivery(notification)
    return notification


def create_notifications_for_alert_opened(alert):
    recipients = get_alert_recipients(alert)
    created = []
    subject = f"Alert Opened: {alert.device.name}"
    message = _alert_message(alert)
    for user in recipients:
        for channel in _channels_for_open(alert.severity):
            notification = _create_notification(
                alert,
                user,
                channel,
                subject,
                message,
                action="OPENED",
            )
            if notification:
                created.append(notification)
                _queue_delivery(notification)
    return created


def create_notifications_for_alert_resolved(alert):
    recipients = get_alert_recipients(alert)
    created = []
    subject = f"Alert Resolved: {alert.device.name}"
    message = _alert_message(alert)
    for user in recipients:
        for channel in _channels_for_resolved(alert.severity):
            notification = _create_notification(
                alert,
                user,
                channel,
                subject,
                message,
                action="RESOLVED",
            )
            if notification:
                created.append(notification)
                _queue_delivery(notification)
    return created
