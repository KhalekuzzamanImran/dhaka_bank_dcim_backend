from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone


@dataclass
class RetentionResult:
    examined: int = 0
    deleted: int = 0
    skipped: int = 0
    by_table: dict[str, int] = field(default_factory=dict)

    def as_dict(self):
        return {
            "examined": self.examined,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "by_table": self.by_table,
        }


def _delete_batches(queryset, *, table_name: str, batch_size: int, result: RetentionResult, dry_run: bool):
    while True:
        primary_keys = list(queryset.order_by("pk").values_list("pk", flat=True)[:batch_size])
        if not primary_keys:
            return
        result.examined += len(primary_keys)
        if dry_run:
            result.skipped += len(primary_keys)
            return
        with transaction.atomic():
            deleted, _ = queryset.model.objects.filter(pk__in=primary_keys).delete()
        result.deleted += deleted
        result.by_table[table_name] = result.by_table.get(table_name, 0) + deleted


def cleanup_retention(*, now=None, batch_size=None, dry_run=None):
    from apps.alerts.models import AlertComment, AlertEvent, AlertEventLog, AlertStatus
    from apps.audit.models import AuditLog
    from apps.notifications.models import Notification, NotificationDelivery, NotificationStatus
    from apps.reports.models import (
        ReportDelivery,
        ReportJob,
        ReportJobStatus,
        ReportScheduleDelivery,
        ReportScheduleDeliveryStatus,
        ReportScheduleRun,
        ReportScheduleRunStatus,
    )
    from apps.telemetry.models import DeviceEvent, TelemetryIngestLog
    from apps.traps.models import SNMPTrapEvent

    if not getattr(settings, "RETENTION_CLEANUP_ENABLED", True):
        return {"disabled": True, **RetentionResult().as_dict()}

    effective_now = now or timezone.now()
    effective_batch_size = int(batch_size or settings.RETENTION_CLEANUP_BATCH_SIZE)
    if effective_batch_size <= 0:
        raise ValueError("RETENTION_CLEANUP_BATCH_SIZE must be greater than zero")
    effective_dry_run = settings.RETENTION_CLEANUP_DRY_RUN if dry_run is None else bool(dry_run)
    result = RetentionResult()

    def cutoff(setting_name):
        return effective_now - timedelta(days=getattr(settings, setting_name))

    _delete_batches(
        TelemetryIngestLog.objects.filter(created_at__lt=cutoff("RETENTION_INGEST_LOG_DAYS")),
        table_name="telemetry_ingest_logs",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        DeviceEvent.objects.filter(occurred_at__lt=cutoff("RETENTION_DEVICE_EVENT_DAYS")),
        table_name="device_events",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        SNMPTrapEvent.objects.filter(received_at__lt=cutoff("RETENTION_SNMP_TRAP_EVENT_DAYS")),
        table_name="snmp_trap_events",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        AlertEventLog.objects.filter(created_at__lt=cutoff("RETENTION_ALERT_HISTORY_DAYS")),
        table_name="alert_event_logs",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        AlertComment.objects.filter(created_at__lt=cutoff("RETENTION_ALERT_HISTORY_DAYS")),
        table_name="alert_comments",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        AlertEvent.objects.filter(
            triggered_at__lt=cutoff("RETENTION_ALERT_EVENT_DAYS"),
            status__in=[AlertStatus.RESOLVED, AlertStatus.SUPPRESSED],
        ),
        table_name="alert_events",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        NotificationDelivery.objects.filter(
            created_at__lt=cutoff("RETENTION_NOTIFICATION_DELIVERY_DAYS"),
            status__in=[NotificationStatus.SENT, NotificationStatus.FAILED],
        ),
        table_name="notification_deliveries",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        Notification.objects.filter(
            created_at__lt=cutoff("RETENTION_NOTIFICATION_DAYS"),
            read_at__isnull=False,
        ).exclude(deliveries__status__in=[NotificationStatus.PENDING, NotificationStatus.DELIVERING]).distinct(),
        table_name="notifications",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        AuditLog.objects.filter(created_at__lt=cutoff("RETENTION_AUDIT_LOG_DAYS")),
        table_name="audit_logs",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        ReportScheduleDelivery.objects.filter(
            created_at__lt=cutoff("RETENTION_REPORT_METADATA_DAYS"),
            status__in=[ReportScheduleDeliveryStatus.SENT, ReportScheduleDeliveryStatus.FAILED],
        ),
        table_name="report_schedule_deliveries",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        ReportScheduleRun.objects.filter(
            created_at__lt=cutoff("RETENTION_REPORT_METADATA_DAYS"),
            status__in=[ReportScheduleRunStatus.COMPLETED, ReportScheduleRunStatus.FAILED, ReportScheduleRunStatus.CANCELLED],
        ).exclude(deliveries__status__in=[ReportScheduleDeliveryStatus.PENDING, ReportScheduleDeliveryStatus.DELIVERING]).distinct(),
        table_name="report_schedule_runs",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        ReportDelivery.objects.filter(
            created_at__lt=cutoff("RETENTION_REPORT_METADATA_DAYS"),
            status__in=["SENT", "FAILED"],
        ),
        table_name="report_deliveries",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    _delete_batches(
        ReportJob.objects.filter(
            created_at__lt=cutoff("RETENTION_REPORT_METADATA_DAYS"),
            status__in=[ReportJobStatus.COMPLETED, ReportJobStatus.FAILED, ReportJobStatus.CANCELLED],
            artifacts__isnull=True,
            deliveries__isnull=True,
        ).distinct(),
        table_name="report_jobs",
        batch_size=effective_batch_size,
        result=result,
        dry_run=effective_dry_run,
    )
    return {"disabled": False, "dry_run": effective_dry_run, **result.as_dict()}
