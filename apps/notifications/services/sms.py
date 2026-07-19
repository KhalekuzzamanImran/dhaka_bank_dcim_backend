from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ET.register_namespace("soap", "http://schemas.xmlsoap.org/soap/envelope/")
ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
ET.register_namespace("", "http://dhakabank.com.bd/")


def _recipient_phone(recipient):
    if not recipient:
        return None
    for field in ("phone", "mobile", "phone_number", "msisdn"):
        value = getattr(recipient, field, None)
        if value:
            return str(value).strip()
    return None


def _normalize_bangladesh_mobile(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if len(digits) == 11 and digits.startswith("01"):
        return digits
    if len(digits) == 13 and digits.startswith("880") and digits[3] == "1":
        return digits[2:]
    if len(digits) == 12 and digits.startswith("88") and digits[2] == "0" and digits[3] == "1":
        return digits[2:]
    raise ValueError("Recipient phone/mobile must be a valid 11-digit Bangladeshi mobile number.")


def _soap_local_name(element):
    if element is None:
        return None
    tag = getattr(element, "tag", "")
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _find_soap_text(root, local_name: str):
    for element in root.iter():
        if _soap_local_name(element) == local_name:
            return (element.text or "").strip()
    return ""


def _parse_soap_delivery_response(content: bytes) -> dict:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("Invalid SMS gateway response.") from exc

    return {
        "status_id": _find_soap_text(root, "StatusId"),
        "status": _find_soap_text(root, "Status"),
        "sms_csms_id": _find_soap_text(root, "SmsCsmsId"),
        "sms_ref_no": _find_soap_text(root, "SmsRefNo"),
    }


def _build_sms_soap_envelope(*, phone: str, message: str, sms_type: str, user_id: str, password: str) -> bytes:
    envelope = ET.Element(
        "{http://schemas.xmlsoap.org/soap/envelope/}Envelope",
    )
    body = ET.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    request = ET.SubElement(body, "{http://dhakabank.com.bd/}DoSendSms")
    ET.SubElement(request, "{http://dhakabank.com.bd/}mobileNumber").text = phone
    ET.SubElement(request, "{http://dhakabank.com.bd/}smsText").text = message
    ET.SubElement(request, "{http://dhakabank.com.bd/}smsType").text = sms_type
    ET.SubElement(request, "{http://dhakabank.com.bd/}userId").text = user_id
    ET.SubElement(request, "{http://dhakabank.com.bd/}password").text = password
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


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


def _send_soap_sms(phone: str, message: str):
    service_url = (
        getattr(settings, "SMS_SOAP_SERVICE_URL", "")
        or getattr(settings, "SMS_API_URL", "")
    )
    user_id = getattr(settings, "SMS_SOAP_USER_ID", "")
    password = getattr(settings, "SMS_SOAP_PASSWORD", "")
    sms_type = str(getattr(settings, "SMS_SOAP_SMS_TYPE", "E") or "E").strip().upper()
    timeout = getattr(settings, "SMS_TIMEOUT_SECONDS", 15)

    if not service_url:
        raise ValueError("SMS_SOAP_SERVICE_URL is not configured")
    if not user_id:
        raise ValueError("SMS_SOAP_USER_ID is not configured")
    if not password:
        raise ValueError("SMS_SOAP_PASSWORD is not configured")
    if sms_type not in {"E", "B"}:
        raise ValueError("SMS_SOAP_SMS_TYPE must be either 'E' or 'B'")

    normalized_phone = _normalize_bangladesh_mobile(phone)
    envelope = _build_sms_soap_envelope(
        phone=normalized_phone,
        message=message,
        sms_type=sms_type,
        user_id=user_id,
        password=password,
    )

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": '"http://dhakabank.com.bd/DoSendSms"',
    }

    logger.info("Sending SMS notification via SOAP phone=%s url=%s", normalized_phone, service_url)
    response = requests.post(service_url, data=envelope, headers=headers, timeout=timeout)
    response.raise_for_status()

    gateway_response = _parse_soap_delivery_response(response.content)
    status_id = gateway_response["status_id"]
    status = gateway_response["status"]
    sms_csms_id = gateway_response["sms_csms_id"]
    sms_ref_no = gateway_response["sms_ref_no"]

    if status_id != "1" and status.upper() not in {"SUCCESSFULL", "SUCCESSFUL"}:
        gateway_status = status or status_id or "FAILED"
        raise ValueError(f"SMS gateway rejected the message: {gateway_status}")

    logger.info(
        "SMS SOAP delivery succeeded phone=%s status_id=%s status=%s csms_id=%s ref_no=%s",
        normalized_phone,
        status_id,
        status,
        sms_csms_id,
        sms_ref_no,
    )
    return {
        "backend": "soap",
        "phone": normalized_phone,
        "service_url": service_url,
        **gateway_response,
    }


def send_sms_notification(notification):
    recipient = getattr(notification, "recipient", None)
    phone = _recipient_phone(recipient)
    if not phone:
        raise ValueError("Recipient phone/mobile is missing for SMS notification")

    return send_sms_message(phone, notification.message)


def send_sms_message(phone: str, message: str):
    """Send a standalone SMS for workflows without a User recipient."""
    backend = str(getattr(settings, "SMS_BACKEND", "console")).strip().lower()
    if backend == "console":
        _send_console_sms(phone, message)
        return {"backend": "console", "phone": phone}
    if backend == "http":
        response = _send_http_sms(phone, message)
        return {"backend": "http", "phone": phone, "status_code": getattr(response, "status_code", None)}
    if backend == "soap":
        return _send_soap_sms(phone, message)
    raise ValueError(f"Unsupported SMS_BACKEND: {backend}")
