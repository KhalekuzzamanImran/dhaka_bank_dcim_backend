from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _recipient_phone(recipient):
    if not recipient:
        return None
    for field in ("phone", "mobile", "phone_number", "msisdn"):
        value = getattr(recipient, field, None)
        if value:
            return str(value).strip()
    return None


def _send_console_sms(phone: str, message: str):
    logger.info("[TEST SMS] To: %s | Message: %s", phone, message)


def _send_http_sms(phone: str, message: str):
    api_url = getattr(settings, "SMS_API_URL", "")
    api_key = getattr(settings, "SMS_API_KEY", "")
    sender = getattr(settings, "SMS_DEFAULT_SENDER", "DCIM")
    timeout = getattr(settings, "SMS_TIMEOUT_SECONDS", 15)
    if not api_url:
        raise ValueError("SMS_API_URL is not configured")

    payload = {
        "sender": sender,
        "to": phone,
        "message": message,
    }
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key

    logger.info("Sending SMS notification via HTTP phone=%s url=%s", phone, api_url)
    response = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    logger.info("SMS HTTP delivery succeeded phone=%s status=%s", phone, response.status_code)
    return response


def send_sms_notification(notification):
    recipient = getattr(notification, "recipient", None)
    phone = _recipient_phone(recipient)
    if not phone:
        raise ValueError("Recipient phone/mobile is missing for SMS notification")

    message = notification.message
    backend = str(getattr(settings, "SMS_BACKEND", "console")).strip().lower()
    if backend == "console":
        _send_console_sms(phone, message)
        return notification
    if backend == "http":
        _send_http_sms(phone, message)
        return notification
    raise ValueError(f"Unsupported SMS_BACKEND: {backend}")
