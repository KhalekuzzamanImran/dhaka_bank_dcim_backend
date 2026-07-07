from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_email_notification(notification):
    recipient = getattr(notification, "recipient", None)
    email = getattr(recipient, "email", None) if recipient else None
    if not email:
        raise ValueError("Recipient email is missing for EMAIL notification")

    subject = notification.subject or "DCIM Notification"
    logger.info("Sending email notification notification=%s recipient=%s", notification.pk, email)
    send_mail(
        subject=subject,
        message=notification.message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[email],
        fail_silently=False,
    )
    logger.info("Email sent notification=%s recipient=%s", notification.pk, email)
    return notification
