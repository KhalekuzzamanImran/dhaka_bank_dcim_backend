from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)


def _notification_from_target(target):
    return getattr(target, "notification", None) or target


def _email_from_target(target):
    if getattr(target, "recipient_address", None):
        return str(target.recipient_address).strip()
    notification = _notification_from_target(target)
    recipient = getattr(notification, "recipient", None)
    return getattr(recipient, "email", None) if recipient else None


def send_email_notification(target, *, subject=None, body=None, attachments=None):
    notification = _notification_from_target(target)
    email = _email_from_target(target)
    if not email:
        raise ValueError("Recipient email is missing for EMAIL notification")

    subject = subject or notification.subject or "DCIM Notification"
    message_body = body or notification.message
    logger.info("Sending email notification notification=%s recipient=%s", notification.pk, email)
    message = EmailMessage(
        subject=subject,
        body=message_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[email],
    )
    for attachment in attachments or []:
        if not attachment:
            continue
        filename = attachment.get("filename")
        payload = attachment.get("content")
        mimetype = attachment.get("mimetype")
        if filename and payload is not None:
            message.attach(filename, payload, mimetype)
    sent_count = message.send(fail_silently=False)
    logger.info("Email sent notification=%s recipient=%s", notification.pk, email)
    return {
        "backend": "email",
        "recipient": email,
        "sent_count": sent_count,
        "subject": subject,
        "attachment_count": len([attachment for attachment in attachments or [] if attachment]),
    }
