from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _notification_from_target(target):
    return getattr(target, "notification", None) or target


def _email_from_target(target):
    if getattr(target, "recipient_address", None):
        return str(target.recipient_address).strip()
    notification = _notification_from_target(target)
    recipient = getattr(notification, "recipient", None)
    return getattr(recipient, "email", None) if recipient else None


def send_email_notification(target):
    notification = _notification_from_target(target)
    email = _email_from_target(target)
    if not email:
        raise ValueError("Recipient email is missing for EMAIL notification")

    subject = notification.subject or "DCIM Notification"
    logger.info("Sending email notification notification=%s recipient=%s", notification.pk, email)
    sent_count = send_mail(
        subject=subject,
        message=notification.message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[email],
        fail_silently=False,
    )
    logger.info("Email sent notification=%s recipient=%s", notification.pk, email)
    return {"backend": "email", "recipient": email, "sent_count": sent_count}
