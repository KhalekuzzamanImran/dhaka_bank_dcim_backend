from __future__ import annotations

import re


def normalize_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


REPORT_TYPE_LABELS = {
    "alert_summary": "Alert Summary",
    "notification_delivery": "Notification Delivery",
    "device_inventory": "Device Inventory",
    "alert_export": "Alert Export",
    "audit_export": "Audit Export",
    "telemetry_export": "Telemetry Export",
    "room_environment": "Environmental Trends Report",
}

REPORT_TYPE_ALIASES = {
    normalize_key("alert summary"): "alert_summary",
    normalize_key("alert summary report"): "alert_summary",
    normalize_key("notification delivery"): "notification_delivery",
    normalize_key("notification delivery report"): "notification_delivery",
    normalize_key("device inventory"): "device_inventory",
    normalize_key("device inventory report"): "device_inventory",
    normalize_key("environmental trends report"): "room_environment",
    normalize_key("room temperature and humidity report"): "room_environment",
    normalize_key("room environment report"): "room_environment",
    normalize_key("room_environment"): "room_environment",
    normalize_key("alert_summary"): "alert_summary",
    normalize_key("notification_delivery"): "notification_delivery",
    normalize_key("device_inventory"): "device_inventory",
    normalize_key("alert_export"): "alert_export",
    normalize_key("audit_export"): "audit_export",
    normalize_key("alert export"): "alert_export",
    normalize_key("audit export"): "audit_export",
    normalize_key("telemetry export"): "telemetry_export",
    normalize_key("telemetry_export"): "telemetry_export",
}

SUPPORTED_REPORT_TYPES = tuple(REPORT_TYPE_LABELS.keys())
REPORT_TYPE_CHOICES = tuple((code, label) for code, label in REPORT_TYPE_LABELS.items())

REPORT_SCHEDULE_FREQUENCY_LABELS = {
    "DAILY": "Daily",
    "WEEKLY": "Weekly",
    "MONTHLY": "Monthly",
    "QUARTERLY": "Quarterly",
}

REPORT_SCHEDULE_FREQUENCY_ALIASES = {
    normalize_key("daily"): "DAILY",
    normalize_key("weekly"): "WEEKLY",
    normalize_key("monthly"): "MONTHLY",
    normalize_key("quarterly"): "QUARTERLY",
}

REPORT_SCHEDULE_FREQUENCY_CHOICES = tuple((code, label) for code, label in REPORT_SCHEDULE_FREQUENCY_LABELS.items())

REPORT_SCHEDULE_FORMAT_LABELS = {
    "CSV": "CSV",
    "PDF": "PDF",
    "PDF_CSV": "PDF / CSV",
}

REPORT_SCHEDULE_FORMAT_ALIASES = {
    normalize_key("csv"): "CSV",
    normalize_key("pdf"): "PDF",
    normalize_key("pdf csv"): "PDF_CSV",
    normalize_key("pdf / csv"): "PDF_CSV",
    normalize_key("pdf and csv"): "PDF_CSV",
    normalize_key("pdf + csv"): "PDF_CSV",
}

REPORT_SCHEDULE_FORMAT_CHOICES = tuple((code, label) for code, label in REPORT_SCHEDULE_FORMAT_LABELS.items())


def normalize_report_type(value: object) -> str | None:
    key = normalize_key(value)
    if not key:
        return None
    if key in REPORT_TYPE_ALIASES:
        return REPORT_TYPE_ALIASES[key]
    if key in REPORT_TYPE_LABELS:
        return key
    return None


def normalize_report_frequency(value: object) -> str | None:
    key = normalize_key(value)
    if not key:
        return None
    if key in REPORT_SCHEDULE_FREQUENCY_ALIASES:
        return REPORT_SCHEDULE_FREQUENCY_ALIASES[key]
    if key in REPORT_SCHEDULE_FREQUENCY_LABELS:
        return key
    return None


def normalize_report_format(value: object) -> str | None:
    key = normalize_key(value)
    if not key:
        return None
    if key in REPORT_SCHEDULE_FORMAT_ALIASES:
        return REPORT_SCHEDULE_FORMAT_ALIASES[key]
    if key.upper() in REPORT_SCHEDULE_FORMAT_LABELS:
        return key.upper()
    return None
