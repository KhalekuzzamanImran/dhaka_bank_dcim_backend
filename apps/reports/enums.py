from __future__ import annotations

from django.db import models


class ReportDefinitionCategory(models.TextChoices):
    INVENTORY = "INVENTORY", "Inventory"
    TELEMETRY = "TELEMETRY", "Telemetry"
    POWER = "POWER", "Power"
    ENVIRONMENT = "ENVIRONMENT", "Environment"
    ALERT = "ALERT", "Alert"
    AVAILABILITY = "AVAILABILITY", "Availability"
    NOTIFICATION = "NOTIFICATION", "Notification"
    AUDIT = "AUDIT", "Audit"
    CAPACITY = "CAPACITY", "Capacity"
    COMPLIANCE = "COMPLIANCE", "Compliance"
    EXECUTIVE = "EXECUTIVE", "Executive"
    INCIDENT = "INCIDENT", "Incident"


class ReportScheduleStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    DISABLED = "DISABLED", "Disabled"


class ReportTriggerSource(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    SCHEDULED = "SCHEDULED", "Scheduled"
    EVENT = "EVENT", "Event"


class ReportJobStatusV2(models.TextChoices):
    PENDING = "PENDING", "Pending"
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    PROCESSING = "PROCESSING", "Processing"
    PARTIAL = "PARTIAL", "Partial"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class ReportDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    QUEUED = "QUEUED", "Queued"
    DELIVERING = "DELIVERING", "Delivering"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class ReportArtifactFormat(models.TextChoices):
    CSV = "CSV", "CSV"
    XLSX = "XLSX", "XLSX"
    PDF = "PDF", "PDF"


class ReportRecipientChannel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    SMS = "SMS", "SMS"
