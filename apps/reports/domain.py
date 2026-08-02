from __future__ import annotations

from django.db import models

from .constants import REPORT_TYPE_LABELS, normalize_key, normalize_report_type


class ReportCategory(models.TextChoices):
    ALERTS = "ALERTS", "Alerts"
    NOTIFICATIONS = "NOTIFICATIONS", "Notifications"
    INVENTORY = "INVENTORY", "Inventory"
    TELEMETRY = "TELEMETRY", "Telemetry"
    AUDIT = "AUDIT", "Audit"
    ENVIRONMENT = "ENVIRONMENT", "Environment"


class ReportJobTriggerSource(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    SCHEDULED = "SCHEDULED", "Scheduled"
    RUN_NOW = "RUN_NOW", "Run Now"
    API = "API", "API"
    RETRY = "RETRY", "Retry"
    SYSTEM = "SYSTEM", "System"


class ReportJobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PENDING = QUEUED
    PROCESSING = RUNNING
    COMPLETED = SUCCEEDED

    choices = (
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (SUCCEEDED, "Succeeded"),
        (PARTIALLY_SUCCEEDED, "Partially Succeeded"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
    )
    values = (QUEUED, RUNNING, SUCCEEDED, PARTIALLY_SUCCEEDED, FAILED, CANCELLED)


class ReportScheduleStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    DISABLED = "DISABLED", "Disabled"
    EXPIRED = "EXPIRED", "Expired"


class ReportRecipientChannel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    SMS = "SMS", "SMS"


class ReportRecipientType(models.TextChoices):
    TO = "TO", "To"
    CC = "CC", "CC"
    BCC = "BCC", "BCC"


class ReportArtifactType(models.TextChoices):
    PRIMARY = "PRIMARY", "Primary"
    RAW_DATA = "RAW_DATA", "Raw Data"
    PREVIEW = "PREVIEW", "Preview"
    SUPPORTING = "SUPPORTING", "Supporting"


class ReportArtifactStatus(models.TextChoices):
    GENERATING = "GENERATING", "Generating"
    AVAILABLE = "AVAILABLE", "Available"
    EXPIRED = "EXPIRED", "Expired"
    DELETED = "DELETED", "Deleted"


class ReportDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENDING = "SENDING", "Sending"
    ACCEPTED = "ACCEPTED", "Accepted"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"


def canonical_report_definition_code(value: object) -> str | None:
    code = normalize_report_type(value)
    if code:
        return code
    normalized = normalize_key(value)
    if not normalized:
        return None
    if normalized in REPORT_TYPE_LABELS:
        return normalized
    return None


def report_definition_seed(code: str) -> dict | None:
    definitions = {entry["code"]: entry for entry in REPORT_DEFINITION_SEEDS}
    return definitions.get(code)


REPORT_DEFINITION_SEEDS = [
    {
        "code": "alert_summary",
        "name": "Alert Summary",
        "description": "Summary of alert activity, severity breakdown, and resolution counts.",
        "category": ReportCategory.ALERTS,
        "handler_key": "legacy.alert_summary",
        "parameter_schema": {
            "required_filters": [],
            "optional_filters": [
                "date_from",
                "date_to",
                "data_center_id",
                "device_id",
                "device_model_id",
                "device_type_id",
                "room_id",
                "rack_id",
                "metric_codes",
                "severity",
                "status",
            ],
            "default_columns": ["section", "label", "value"],
            "max_date_range_days": 90,
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": False,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
    {
        "code": "notification_delivery",
        "name": "Notification Delivery",
        "description": "Notification delivery status grouped by channel and lifecycle state.",
        "category": ReportCategory.NOTIFICATIONS,
        "handler_key": "legacy.notification_delivery",
        "parameter_schema": {
            "required_filters": [],
            "optional_filters": [
                "date_from",
                "date_to",
                "recipient_id",
                "channel",
                "status",
                "alert_id",
                "device_id",
            ],
            "default_columns": ["section", "label", "value"],
            "max_date_range_days": 90,
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": False,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
    {
        "code": "device_inventory",
        "name": "Device Inventory",
        "description": "Active device inventory scoped to the organization and data center.",
        "category": ReportCategory.INVENTORY,
        "handler_key": "legacy.device_inventory",
        "parameter_schema": {
            "required_filters": [],
            "optional_filters": [
                "data_center_id",
                "room_id",
                "rack_id",
                "device_id",
                "device_model_id",
                "device_type_id",
                "status",
                "is_active",
            ],
            "default_columns": [
                "organization",
                "data_center",
                "room",
                "rack",
                "device",
                "code",
                "hostname",
                "ip_address",
                "device_type",
                "device_model",
                "vendor",
                "status",
                "is_active",
                "last_seen",
            ],
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": False,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
    {
        "code": "alert_export",
        "name": "Alert Export",
        "description": "Exports detailed alert history with device, metric, severity, status, acknowledgement, and resolution details.",
        "category": ReportCategory.ALERTS,
        "handler_key": "legacy.alert_export",
        "parameter_schema": {
            "required_filters": ["date_from", "date_to"],
            "optional_filters": [
                "device_id",
                "device_model_id",
                "device_type_id",
                "data_center_id",
                "room_id",
                "rack_id",
                "metric_codes",
                "severity",
                "status",
                "source",
            ],
            "default_columns": [
                "triggered_at",
                "resolved_at",
                "organization",
                "data_center",
                "room",
                "rack",
                "device",
                "device_model",
                "metric",
                "severity",
                "status",
                "message",
                "occurrence_count",
                "acknowledged_by",
                "resolved_by",
            ],
            "max_date_range_days": 90,
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": False,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
    {
        "code": "audit_export",
        "name": "Audit Export",
        "description": "Exports audit log records for user actions, system actions, resources, and IP activity.",
        "category": ReportCategory.AUDIT,
        "handler_key": "legacy.audit_export",
        "parameter_schema": {
            "required_filters": ["date_from", "date_to"],
            "optional_filters": ["actor_id", "actions", "resource_type", "resource_id", "ip_address"],
            "default_columns": [
                "created_at",
                "actor",
                "action",
                "resource_type",
                "resource_id",
                "organization",
                "message",
                "ip_address",
                "user_agent",
            ],
            "max_date_range_days": 90,
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": False,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
    {
        "code": "telemetry_export",
        "name": "Telemetry Export",
        "description": "Exports selected historical telemetry metrics for devices, models, rooms, racks, or data centers.",
        "category": ReportCategory.TELEMETRY,
        "handler_key": "legacy.telemetry_export",
        "parameter_schema": {
            "required_filters": ["date_from", "date_to", "metric_codes"],
            "optional_filters": [
                "device_id",
                "device_model_id",
                "device_type_id",
                "data_center_id",
                "room_id",
                "rack_id",
            ],
            "default_columns": [
                "timestamp",
                "organization",
                "data_center",
                "room",
                "rack",
                "device",
                "device_model",
                "device_type",
                "metric_code",
                "metric_name",
                "value",
                "unit",
                "quality",
            ],
            "max_date_range_days": 31,
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": True,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
    {
        "code": "room_environment",
        "name": "Environmental Trends Report",
        "description": "Exports room temperature and humidity readings for the selected time window.",
        "category": ReportCategory.ENVIRONMENT,
        "handler_key": "legacy.room_environment",
        "parameter_schema": {
            "required_filters": ["date_from", "date_to"],
            "optional_filters": ["data_center_id", "room_id", "device_id"],
            "default_columns": [
                "timestamp",
                "organization",
                "data_center",
                "room",
                "device",
                "metric_code",
                "metric_name",
                "value",
                "unit",
                "quality",
            ],
            "max_date_range_days": 31,
        },
        "supported_formats": ["CSV"],
        "supports_scheduling": True,
        "supports_raw_attachment": False,
        "supports_charts": False,
        "required_permission": "report.view",
        "version": 1,
        "is_active": True,
    },
]
