from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.common.access import get_access_scope
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus

logger = logging.getLogger(__name__)

_ALERT_PERMISSION_CODES = {"alert.view", "alert.acknowledge"}


def _alert_scope_condition(alert):
    conditions = Q(is_superuser=True)
    if alert.organization_id:
        conditions |= Q(data_center_roles__organization_id=alert.organization_id)
    if alert.data_center_id:
        conditions |= Q(data_center_roles__data_center_id=alert.data_center_id)
    room_id = getattr(getattr(alert.device, "room", None), "id", None)
    if room_id:
        conditions |= Q(data_center_roles__room_id=room_id)
    rack_id = getattr(getattr(alert.device, "rack", None), "id", None)
    if rack_id:
        conditions |= Q(data_center_roles__rack_id=rack_id)
    if alert.device_id:
        conditions |= Q(data_center_roles__device_id=alert.device_id)
    return conditions


def _recipient_queryset(alert):
    from apps.accounts.models import User

    permission_q = Q(data_center_roles__role__role_permissions__permission__code__in=list(_ALERT_PERMISSION_CODES))
    staff_q = Q(is_staff=True)
    base_q = _alert_scope_condition(alert)
    return (
        User.objects.filter(is_active=True)
        .filter(base_q)
        .filter(Q(is_superuser=True) | (staff_q & Q(data_center_roles__is_active=True)) | (permission_q & Q(data_center_roles__is_active=True)))
        .distinct()
    )


def _has_alert_scope(user, alert):
    if not user or not user.is_active:
        return False
    if user.is_superuser:
        return True
    scope = get_access_scope(user)
    if scope.get("global_access"):
        return True
    if alert.organization_id and alert.organization_id in scope["organization_ids"]:
        return True
    if alert.data_center_id and alert.data_center_id in scope["data_center_ids"]:
        return True
    room_id = getattr(getattr(alert.device, "room", None), "id", None)
    if room_id and room_id in scope["room_ids"]:
        return True
    rack_id = getattr(getattr(alert.device, "rack", None), "id", None)
    if rack_id and rack_id in scope["rack_ids"]:
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
    return list(_recipient_queryset(alert).order_by("username", "id"))


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


def _notification_exists(dedupe_key: str) -> bool:
    return Notification.objects.filter(dedupe_key=dedupe_key).exists()


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


def get_escalation_recipients(alert, policy):
    from apps.accounts.models import User

    recipients = []
    seen_ids = set()

    def _append(user):
        if not user or not user.is_active or user.pk in seen_ids:
            return
        if not _has_alert_scope(user, alert):
            return
        seen_ids.add(user.pk)
        recipients.append(user)

    if getattr(policy, "target_users", None) is not None:
        try:
            target_users = (
                policy.target_users.filter(is_active=True)
                .filter(_alert_scope_condition(alert))
                .distinct()
                .order_by("username", "id")
            )
            for user in target_users:
                _append(user)
        except Exception:
            logger.exception("Failed to read escalation policy target users policy=%s", getattr(policy, "pk", None))

    if getattr(policy, "target_role_id", None):
        role_users = (
            User.objects.filter(
                is_active=True,
                data_center_roles__is_active=True,
                data_center_roles__role_id=policy.target_role_id,
            )
            .filter(_alert_scope_condition(alert))
            .distinct()
            .order_by("username", "id")
        )
        for user in role_users:
            _append(user)

    if not recipients:
        for user in User.objects.filter(is_superuser=True, is_active=True).order_by("username", "id"):
            _append(user)
    return recipients


def _escalation_message(alert, policy):
    severity = (alert.severity or "UNKNOWN").upper()
    device_name = getattr(alert.device, "name", "unknown device")
    base = alert.message or ""
    minutes = policy.if_not_acknowledged_minutes if alert.status == "OPEN" else policy.if_not_resolved_minutes
    if alert.status == "OPEN" and policy.if_not_acknowledged_minutes is not None:
        return f"ESCALATION: {severity} alert {base} on {device_name} has not been acknowledged for {minutes} minutes."
    if policy.if_not_resolved_minutes is not None:
        return f"ESCALATION: {severity} alert {base} on {device_name} has not been resolved for {minutes} minutes."
    return f"ESCALATION: {severity} alert {base} on {device_name} requires attention."


def _create_escalation_notification(alert, policy, user, channel, subject, message):
    if channel == NotificationChannel.EMAIL and not getattr(user, "email", None):
        return None
    if channel == NotificationChannel.SMS:
        phone = None
        for field in ("phone", "mobile", "phone_number", "msisdn"):
            value = getattr(user, field, None)
            if value:
                phone = str(value).strip()
                break
        if not phone:
            return None

    dedupe_key = f"alert:{alert.pk}:ESCALATED:{policy.pk}:{channel}:{user.pk}"
    if _notification_exists(dedupe_key):
        logger.info(
            "Skipping duplicate escalation notification alert=%s policy=%s user=%s channel=%s",
            alert.pk,
            policy.pk,
            user.pk,
            channel,
        )
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
                "action": "ESCALATED",
                "policy_id": str(policy.pk),
            },
        )
    except IntegrityError:
        return None

    logger.info(
        "Escalation notification created notification=%s alert=%s policy=%s user=%s channel=%s",
        notification.pk,
        alert.pk,
        policy.pk,
        user.pk,
        channel,
    )
    return notification


def create_notifications_for_alert_escalated(alert, policy):
    recipients = get_escalation_recipients(alert, policy)
    created = []
    subject = f"ALERT ESCALATED: {alert.device.name}"
    message = _escalation_message(alert, policy)
    channels = [policy.channel or NotificationChannel.WEB]

    for user in recipients:
        for channel in channels:
            notification = _create_escalation_notification(alert, policy, user, channel, subject, message)
            if notification:
                created.append(notification)
                _queue_delivery(notification)
    return created
