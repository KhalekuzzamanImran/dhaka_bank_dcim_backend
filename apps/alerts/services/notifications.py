from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.access import get_access_scope
from apps.notifications.models import Notification, NotificationChannel, NotificationDelivery, NotificationStatus
from apps.notifications.services import queue_notification_delivery

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


def _logical_dedupe_key(alert, user, action: str, *, policy=None) -> str:
    if action == "ESCALATED" and policy is not None:
        return f"alert:{alert.pk}:{action}:{policy.pk}:{user.pk}"
    return f"alert:{alert.pk}:{action}:{user.pk}"


def _logical_metadata(alert, action: str, *, policy=None) -> dict:
    metadata = {
        "alert_event_id": str(alert.pk),
        "alert_status": alert.status,
        "severity": alert.severity,
        "device_id": str(alert.device_id) if alert.device_id else None,
        "metric_id": str(alert.metric_id) if alert.metric_id else None,
        "action": action,
    }
    if policy is not None:
        metadata["policy_id"] = str(policy.pk)
    return metadata


def _recipient_address_for_channel(user, channel: str):
    if channel == NotificationChannel.EMAIL:
        email = getattr(user, "email", None)
        return str(email).strip() if email else None
    if channel == NotificationChannel.SMS:
        return _recipient_phone(user)
    return None


def _delivery_metadata(logical_metadata: dict, channel: str, recipient_address: str | None) -> dict:
    metadata = dict(logical_metadata)
    metadata["channel"] = channel
    if recipient_address:
        metadata["recipient_address"] = recipient_address
    return metadata


def _queue_delivery(target):
    return queue_notification_delivery(target)


def _create_logical_notification(alert, user, subject, message, *, action: str, policy=None):
    logical_key = _logical_dedupe_key(alert, user, action, policy=policy)
    defaults = {
        "organization": alert.organization,
        "recipient": user,
        "subject": subject,
        "message": message,
        "metadata": _logical_metadata(alert, action, policy=policy),
        "channel": None,
        "status": None,
        "sent_at": None,
        "error_message": None,
    }
    notification, created = Notification.objects.get_or_create(dedupe_key=logical_key, defaults=defaults)
    if not created:
        logical_metadata = _logical_metadata(alert, action, policy=policy)
        current_metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
        merged_metadata = dict(current_metadata)
        changed = False
        for key, value in logical_metadata.items():
            if key not in merged_metadata and value is not None:
                merged_metadata[key] = value
                changed = True
        if changed:
            notification.metadata = merged_metadata
            notification.save(update_fields=["metadata", "updated_at"])
    return notification, created


def _create_delivery(notification, channel: str, recipient_address: str | None, *, action: str, policy=None):
    logical_metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
    now = timezone.now()

    defaults = {
        "status": NotificationStatus.SENT if channel == NotificationChannel.WEB else NotificationStatus.PENDING,
        "recipient_address": recipient_address,
        "attempt_count": 0,
        "max_attempts": 3,
        "queued_at": now,
        "delivering_at": now if channel == NotificationChannel.WEB else None,
        "sent_at": now if channel == NotificationChannel.WEB else None,
        "failed_at": None,
        "next_retry_at": None,
        "provider_message_id": None,
        "provider_response": {},
        "error_message": "",
        "metadata": _delivery_metadata(logical_metadata, channel, recipient_address),
    }
    delivery, created = NotificationDelivery.objects.get_or_create(notification=notification, channel=channel, defaults=defaults)
    if not created:
        updates = []
        if recipient_address and not delivery.recipient_address:
            delivery.recipient_address = recipient_address
            updates.append("recipient_address")
        if channel == NotificationChannel.WEB and delivery.status != NotificationStatus.SENT:
            delivery.status = NotificationStatus.SENT
            delivery.sent_at = delivery.sent_at or now
            delivery.delivering_at = delivery.delivering_at or now
            updates.extend(["status", "sent_at", "delivering_at"])
        if updates:
            delivery.save(update_fields=updates + ["updated_at"])
    return delivery, created


def _create_notifications_for_alert(alert, *, action: str, subject: str, message: str, channels: list[str], policy=None):
    created_payloads = []
    recipients = get_alert_recipients(alert)
    for user in recipients:
        with transaction.atomic():
            notification, notification_created = _create_logical_notification(alert, user, subject, message, action=action, policy=policy)
            created_deliveries = []
            for channel in channels:
                recipient_address = _recipient_address_for_channel(user, channel)
                if channel == NotificationChannel.EMAIL and not recipient_address:
                    continue
                if channel == NotificationChannel.SMS and not recipient_address:
                    continue
                delivery, delivery_created = _create_delivery(
                    notification,
                    channel,
                    recipient_address,
                    action=action,
                    policy=policy,
                )
                if delivery_created or delivery.status == NotificationStatus.FAILED:
                    created_deliveries.append(delivery)
                    _queue_delivery(delivery)
            if notification_created or created_deliveries:
                created_payloads.append({
                    "notification": notification,
                    "deliveries": created_deliveries,
                })
    return created_payloads


def create_notifications_for_alert_opened(alert):
    subject = f"Alert Opened: {getattr(alert.device, 'name', 'unknown device')}"
    message = alert.message
    channels = _channels_for_open(alert.severity)
    return _create_notifications_for_alert(alert, action="OPENED", subject=subject, message=message, channels=channels)


def create_notifications_for_alert_resolved(alert):
    subject = f"Alert Resolved: {getattr(alert.device, 'name', 'unknown device')}"
    message = alert.message
    channels = _channels_for_resolved(alert.severity)
    return _create_notifications_for_alert(alert, action="RESOLVED", subject=subject, message=message, channels=channels)


def create_notifications_for_alert_escalated(alert, policy):
    subject = f"ALERT ESCALATED: {getattr(alert.device, 'name', 'unknown device')}"
    severity = (alert.severity or "UNKNOWN").upper()
    device_name = getattr(alert.device, "name", "unknown device")
    base = alert.message or ""
    if alert.status == "OPEN" and policy.if_not_acknowledged_minutes is not None:
        message = (
            f"ESCALATION: {severity} alert {base} on {device_name} "
            f"has not been acknowledged for {policy.if_not_acknowledged_minutes} minutes."
        )
    elif policy.if_not_resolved_minutes is not None:
        message = (
            f"ESCALATION: {severity} alert {base} on {device_name} "
            f"has not been resolved for {policy.if_not_resolved_minutes} minutes."
        )
    else:
        message = f"ESCALATION: {severity} alert {base} on {device_name} requires attention."

    channels = [NotificationChannel.WEB]
    if policy.channel and policy.channel != NotificationChannel.WEB:
        channels.append(policy.channel)
    return _create_notifications_for_alert(alert, action="ESCALATED", subject=subject, message=message, channels=channels, policy=policy)
