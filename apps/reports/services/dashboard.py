from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as time_cls, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone as dj_timezone

from apps.common.access import filter_queryset_for_user, get_access_scope
from .observability import log_report_event, log_report_metric

from ..enums import ReportDeliveryStatus, ReportScheduleStatus, ReportTriggerSource
from ..models import (
    ReportArtifact,
    ReportDefinition,
    ReportDelivery,
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleRun,
    ReportScheduleRunStatus,
    ReportTemplate,
)


logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD_RANGE_DAYS = 30
MAX_DASHBOARD_RANGE_DAYS = 365
RECENT_JOB_LIMIT = 10
RECENT_FAILURE_LIMIT = 10
UPCOMING_SCHEDULE_LIMIT = 10
SCHEDULE_HEALTH_LIMIT = 10
FREQUENT_TEMPLATE_LIMIT = 10
QUEUE_AGE_THRESHOLD_MINUTES = 15


@dataclass(frozen=True)
class DashboardScope:
    user: object
    organization: object | None
    data_center: object | None
    organization_ids: set[str]
    data_center_ids: set[str]
    global_access: bool
    timezone: ZoneInfo
    start_at: datetime
    end_at: datetime
    today_start: datetime
    today_end: datetime


def _safe_write_log(message: str, **payload):
    try:
        logger.info(message, extra={"dashboard": payload})
    except Exception:
        logger.info(message)


def _normalize_related_instance(model, value, *, field_name: str):
    if value in (None, ""):
        return None
    if isinstance(value, model):
        return value
    try:
        return model.objects.get(pk=value)
    except model.DoesNotExist as exc:
        raise ValidationError({field_name: "Invalid value."}) from exc


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    candidate = str(timezone_name or "Asia/Dhaka").strip() or "Asia/Dhaka"
    try:
        return ZoneInfo(candidate)
    except Exception as exc:
        raise ValidationError({"timezone": "Invalid timezone."}) from exc


def _ensure_aware(value, tz: ZoneInfo) -> datetime:
    if value is None:
        return None
    if dj_timezone.is_naive(value):
        return dj_timezone.make_aware(value, tz)
    return value.astimezone(tz)


def _default_range(tz: ZoneInfo) -> tuple[datetime, datetime]:
    now = dj_timezone.now().astimezone(tz)
    start_at = now - timedelta(days=DEFAULT_DASHBOARD_RANGE_DAYS)
    return start_at, now


def _resolve_scope(user, organization=None, data_center=None, timezone_name: str | None = None) -> DashboardScope:
    tz = _resolve_timezone(timezone_name)
    organization = _normalize_related_instance(type(organization), organization, field_name="organization") if organization is not None and not isinstance(organization, object) else organization
    data_center = _normalize_related_instance(type(data_center), data_center, field_name="data_center") if data_center is not None and not isinstance(data_center, object) else data_center

    # Import locally to avoid circular imports in service tests.
    from apps.datacenters.models import DataCenter
    from apps.organizations.models import Organization

    if organization is not None and not isinstance(organization, Organization):
        organization = _normalize_related_instance(Organization, organization, field_name="organization")
    if data_center is not None and not isinstance(data_center, DataCenter):
        data_center = _normalize_related_instance(DataCenter, data_center, field_name="data_center")

    if organization is not None and data_center is not None and data_center.organization_id != organization.id:
        raise ValidationError({"data_center": "Data center must belong to the selected organization."})
    if data_center is not None and organization is None:
        organization = data_center.organization

    access_scope = get_access_scope(user)
    global_access = bool(access_scope["global_access"])
    accessible_org_ids = {str(value) for value in access_scope["organization_ids"]}
    accessible_data_center_ids = {str(value) for value in access_scope["data_center_ids"]}

    if organization is not None and not global_access and str(organization.id) not in accessible_org_ids:
        raise ValidationError({"organization": "You do not have access to this organization."})
    if data_center is not None and not global_access and str(data_center.id) not in accessible_data_center_ids:
        raise ValidationError({"data_center": "You do not have access to this data center."})

    if organization is None and data_center is None and not global_access and not accessible_org_ids and not accessible_data_center_ids:
        raise ValidationError({"organization": "You do not have access to any reporting scope."})

    start_at, end_at = _default_range(tz)
    today = dj_timezone.now().astimezone(tz).date()
    today_start = dj_timezone.make_aware(datetime.combine(today, time_cls.min), tz)
    today_end = dj_timezone.make_aware(datetime.combine(today, time_cls.max), tz)

    return DashboardScope(
        user=user,
        organization=organization,
        data_center=data_center,
        organization_ids=accessible_org_ids,
        data_center_ids=accessible_data_center_ids,
        global_access=global_access,
        timezone=tz,
        start_at=start_at,
        end_at=end_at,
        today_start=today_start,
        today_end=today_end,
    )


def _apply_scope(qs, scope: DashboardScope, *, organization_field: str = "organization", data_center_field: str = "data_center"):
    if scope.organization is not None:
        lookup = {organization_field: scope.organization}
        qs = qs.filter(**lookup)
    if scope.data_center is not None:
        lookup = {data_center_field: scope.data_center}
        qs = qs.filter(**lookup)

    if hasattr(qs, "model"):
        qs = filter_queryset_for_user(
            qs,
            scope.user,
            access_scope="mixed",
            organization_field=organization_field,
            data_center_field=data_center_field,
        )
    return qs


def _local_date_range(scope: DashboardScope) -> list[date]:
    start_date = scope.start_at.astimezone(scope.timezone).date()
    end_date = scope.end_at.astimezone(scope.timezone).date()
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _format_datetime(value, tz: ZoneInfo):
    if not value:
        return None
    if dj_timezone.is_naive(value):
        value = dj_timezone.make_aware(value, tz)
    return value.astimezone(tz).isoformat()


def _mask_recipient(recipient: str | None) -> str | None:
    if not recipient:
        return None
    text = str(recipient).strip()
    if not text:
        return None
    if "@" in text:
        user, _, domain = text.partition("@")
        return f"{user[:2]}***@{domain}"
    if len(text) <= 4:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


def _delivery_summary_for_job(job: ReportJob) -> dict[str, str]:
    summary: dict[str, str] = {}
    deliveries = getattr(job, "deliveries", None)
    if deliveries is None:
        return summary
    for delivery in deliveries.all():
        summary[str(delivery.channel)] = str(delivery.status)
    return summary


def _artifact_formats_for_job(job: ReportJob) -> list[str]:
    artifacts = getattr(job, "artifacts", None)
    if artifacts is None:
        return []
    seen = set()
    values = []
    for artifact in artifacts.all():
        fmt = str(artifact.format).upper()
        if fmt in seen:
            continue
        seen.add(fmt)
        values.append(fmt)
    return values


def _summary_for_scope(scope: DashboardScope) -> dict:
    definitions_qs = ReportDefinition.objects.all()
    templates_qs = _apply_scope(ReportTemplate.objects.all(), scope, organization_field="organization")
    jobs_qs = _apply_scope(ReportJob.objects.all(), scope, organization_field="organization", data_center_field="data_center")
    schedules_qs = _apply_scope(ReportSchedule.objects.all(), scope, organization_field="organization", data_center_field="data_center")
    artifacts_qs = _apply_scope(ReportArtifact.objects.select_related("job"), scope, organization_field="job__organization", data_center_field="job__data_center")
    deliveries_qs = _apply_scope(ReportDelivery.objects.select_related("job"), scope, organization_field="job__organization", data_center_field="job__data_center")

    job_counts = jobs_qs.aggregate(
        total_jobs=Count("id"),
        completed_jobs=Count("id", filter=Q(status="COMPLETED")),
        running_jobs=Count("id", filter=Q(status__in=["RUNNING", "PROCESSING"])),
        queued_jobs=Count("id", filter=Q(status="QUEUED")),
        failed_jobs=Count("id", filter=Q(status="FAILED")),
        cancelled_jobs=Count("id", filter=Q(status="CANCELLED")),
        jobs_generated_today=Count("id", filter=Q(created_at__gte=scope.today_start, created_at__lte=scope.today_end)),
    )
    artifact_counts = artifacts_qs.aggregate(
        total_artifacts=Count("id"),
        total_artifact_size_bytes=Sum("size_bytes"),
    )
    schedule_counts = schedules_qs.aggregate(
        total_schedules=Count("id"),
        active_schedules=Count("id", filter=Q(status=ReportScheduleStatus.ACTIVE)),
        paused_schedules=Count("id", filter=Q(status=ReportScheduleStatus.PAUSED)),
        disabled_schedules=Count("id", filter=Q(status=ReportScheduleStatus.DISABLED)),
        schedules_due_today=Count(
            "id",
            filter=Q(status=ReportScheduleStatus.ACTIVE, next_run_at__gte=scope.today_start, next_run_at__lte=scope.today_end),
        ),
    )
    delivery_counts = deliveries_qs.aggregate(
        total_deliveries=Count("id"),
        pending_deliveries=Count("id", filter=Q(status=ReportDeliveryStatus.PENDING)),
        queued_deliveries=Count("id", filter=Q(status=ReportDeliveryStatus.QUEUED)),
        delivering_deliveries=Count("id", filter=Q(status=ReportDeliveryStatus.DELIVERING)),
        sent_deliveries=Count("id", filter=Q(status=ReportDeliveryStatus.SENT)),
        failed_deliveries=Count("id", filter=Q(status=ReportDeliveryStatus.FAILED)),
    )
    final_deliveries = (delivery_counts["sent_deliveries"] or 0) + (delivery_counts["failed_deliveries"] or 0)
    delivery_success_rate = None if final_deliveries == 0 else round(((delivery_counts["sent_deliveries"] or 0) / final_deliveries) * 100, 2)

    return {
        "total_definitions": definitions_qs.count(),
        "active_definitions": definitions_qs.filter(is_active=True).count(),
        "total_templates": templates_qs.count(),
        "active_templates": templates_qs.filter(is_active=True).count(),
        **job_counts,
        **artifact_counts,
        **schedule_counts,
        **delivery_counts,
        "delivery_success_rate": delivery_success_rate,
    }


def _generation_trend(scope: DashboardScope) -> list[dict]:
    jobs_qs = _apply_scope(ReportJob.objects.all(), scope, organization_field="organization", data_center_field="data_center")
    rows = (
        jobs_qs.filter(created_at__gte=scope.start_at, created_at__lte=scope.end_at)
        .annotate(day=TruncDate("created_at", tzinfo=scope.timezone))
        .values("day")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="COMPLETED")),
            failed=Count("id", filter=Q(status="FAILED")),
        )
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}
    trend = []
    for day in _local_date_range(scope):
        row = by_day.get(day)
        trend.append(
            {
                "date": day.isoformat(),
                "total": int(row["total"]) if row else 0,
                "completed": int(row["completed"]) if row else 0,
                "failed": int(row["failed"]) if row else 0,
            }
        )
    return trend


def _reports_by_definition(scope: DashboardScope) -> list[dict]:
    jobs_qs = _apply_scope(ReportJob.objects.select_related("definition"), scope, organization_field="organization", data_center_field="data_center")
    rows = (
        jobs_qs.filter(created_at__gte=scope.start_at, created_at__lte=scope.end_at)
        .values("definition_id", "definition__code", "definition__name", "definition__category")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="COMPLETED")),
            failed=Count("id", filter=Q(status="FAILED")),
        )
        .order_by("-total", "definition__name")
    )
    results = []
    for row in rows:
        results.append(
            {
                "code": row["definition__code"] or "LEGACY_CUSTOM",
                "name": row["definition__name"] or "Legacy / Custom",
                "category": row["definition__category"] or "LEGACY",
                "total": int(row["total"] or 0),
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
            }
        )
    return results


def _job_display_name(job: ReportJob | None) -> str:
    if not job:
        return "Legacy / Custom"
    return (
        getattr(job.template, "name", None)
        or getattr(job.definition, "name", None)
        or "Legacy / Custom"
    )


def _reports_by_format(scope: DashboardScope) -> list[dict]:
    artifacts_qs = _apply_scope(ReportArtifact.objects.select_related("job"), scope, organization_field="job__organization", data_center_field="job__data_center")
    rows = (
        artifacts_qs.filter(created_at__gte=scope.start_at, created_at__lte=scope.end_at)
        .values("format")
        .annotate(count=Count("id"), size_bytes=Sum("size_bytes"))
        .order_by("format")
    )
    return [
        {
            "format": row["format"],
            "count": int(row["count"] or 0),
            "size_bytes": int(row["size_bytes"] or 0),
        }
        for row in rows
    ]


def _delivery_summary(scope: DashboardScope) -> dict:
    deliveries_qs = _apply_scope(ReportDelivery.objects.select_related("job"), scope, organization_field="job__organization", data_center_field="job__data_center")
    by_status = deliveries_qs.aggregate(
        PENDING=Count("id", filter=Q(status=ReportDeliveryStatus.PENDING)),
        QUEUED=Count("id", filter=Q(status=ReportDeliveryStatus.QUEUED)),
        DELIVERING=Count("id", filter=Q(status=ReportDeliveryStatus.DELIVERING)),
        SENT=Count("id", filter=Q(status=ReportDeliveryStatus.SENT)),
        FAILED=Count("id", filter=Q(status=ReportDeliveryStatus.FAILED)),
    )
    by_channel_rows = (
        deliveries_qs.values("channel")
        .annotate(
            sent=Count("id", filter=Q(status=ReportDeliveryStatus.SENT)),
            failed=Count("id", filter=Q(status=ReportDeliveryStatus.FAILED)),
            total=Count("id"),
        )
        .order_by("channel")
    )
    final_deliveries = (by_status["SENT"] or 0) + (by_status["FAILED"] or 0)
    success_rate = None if final_deliveries == 0 else round(((by_status["SENT"] or 0) / final_deliveries) * 100, 2)
    return {
        "by_status": {key: int(value or 0) for key, value in by_status.items()},
        "by_channel": [
            {
                "channel": row["channel"],
                "sent": int(row["sent"] or 0),
                "failed": int(row["failed"] or 0),
                "total": int(row["total"] or 0),
            }
            for row in by_channel_rows
        ],
        "delivery_success_rate": success_rate,
    }


def _recent_jobs(scope: DashboardScope) -> list[dict]:
    jobs_qs = _apply_scope(
        ReportJob.objects.select_related("organization", "data_center", "definition", "template", "requested_by", "schedule")
        .prefetch_related("artifacts", "deliveries"),
        scope,
        organization_field="organization",
        data_center_field="data_center",
    )
    rows = list(
        jobs_qs.filter(created_at__gte=scope.start_at, created_at__lte=scope.end_at)
        .order_by("-created_at")[:RECENT_JOB_LIMIT]
    )
    results = []
    for job in rows:
        results.append(
            {
                "id": str(job.id),
                "definition_code": getattr(job.definition, "code", None),
                "definition_name": getattr(job.definition, "name", None),
                "template_name": getattr(job.template, "name", None),
                "schedule_name": getattr(job.schedule, "name", None),
                "trigger_source": job.trigger_source,
                "organization_id": str(job.organization_id) if job.organization_id else None,
                "organization_name": getattr(job.organization, "name", None),
                "data_center_id": str(job.data_center_id) if job.data_center_id else None,
                "data_center_name": getattr(job.data_center, "name", None),
                "requested_by": getattr(job.requested_by, "full_name", None)
                or getattr(job.requested_by, "username", None)
                or getattr(job.requested_by, "email", None),
                "status": job.status,
                "progress_percent": job.progress_percent,
                "created_at": _format_datetime(job.created_at, scope.timezone),
                "started_at": _format_datetime(job.started_at, scope.timezone),
                "completed_at": _format_datetime(job.completed_at, scope.timezone),
                "duration_seconds": job.duration_seconds,
                "artifact_formats": _artifact_formats_for_job(job),
                "delivery_summary": _delivery_summary_for_job(job),
                "safe_error_message": (job.error_message or "").strip() or None,
            }
        )
    return results


def _upcoming_schedules(scope: DashboardScope) -> list[dict]:
    schedules_qs = _apply_scope(
        ReportSchedule.objects.select_related("organization", "data_center", "template", "template__definition", "last_job")
        .prefetch_related("structured_recipients"),
        scope,
        organization_field="organization",
        data_center_field="data_center",
    )
    now = dj_timezone.now()
    rows = list(schedules_qs.filter(status=ReportScheduleStatus.ACTIVE).order_by("next_run_at", "created_at")[:UPCOMING_SCHEDULE_LIMIT * 2])
    results = []
    for schedule in rows:
        next_run_at = schedule.next_run_at
        if next_run_at is None or next_run_at < now:
            try:
                next_run_at = schedule.calculate_next_run_at(reference_time=now)
            except Exception:
                next_run_at = schedule.next_run_at
        if next_run_at is None or next_run_at < now:
            continue
        structured = list(getattr(schedule, "_prefetched_objects_cache", {}).get("structured_recipients", []))
        emails = {value for value in schedule.normalize_recipients()}
        sms_values = {
            str(value).strip()
            for value in (schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else [])
            if str(value).strip()
        }
        for row in structured:
            if not row.is_active:
                continue
            if row.channel == "EMAIL" and row.email_address:
                emails.add(str(row.email_address).strip().lower())
            elif row.channel == "SMS" and row.phone_number:
                sms_values.add(str(row.phone_number).strip())
        channels = []
        if emails:
            channels.append("EMAIL")
        if schedule.send_sms or sms_values:
            channels.append("SMS")
        results.append(
            {
                "id": str(schedule.id),
                "name": schedule.name,
                "template_name": getattr(schedule.template, "name", None),
                "definition_code": getattr(schedule.template.definition, "code", None) if getattr(schedule.template, "definition_id", None) else None,
                "definition_name": getattr(schedule.template.definition, "name", None) if getattr(schedule.template, "definition_id", None) else None,
                "frequency": schedule.frequency,
                "timezone": schedule.timezone,
                "next_run_at": _format_datetime(next_run_at, scope.timezone),
                "recipient_count": len(emails) + len(sms_values),
                "channels": channels,
                "primary_format": schedule.primary_format or schedule.output_format,
                "status": schedule.status,
                "last_run_at": _format_datetime(schedule.last_run_at, scope.timezone),
                "last_success_at": _format_datetime(schedule.last_success_at, scope.timezone),
                "last_failure_at": _format_datetime(
                    getattr(schedule.last_job, "failed_at", None)
                    if getattr(schedule.last_job, "status", None) == ReportJobStatus.FAILED
                    else None,
                    scope.timezone,
                ),
            }
        )
    results.sort(key=lambda row: row["next_run_at"] or "")
    return results[:UPCOMING_SCHEDULE_LIMIT]


def _recent_failures(scope: DashboardScope) -> list[dict]:
    jobs_qs = _apply_scope(ReportJob.objects.select_related("organization", "data_center", "definition", "template"), scope, organization_field="organization", data_center_field="data_center")
    deliveries_qs = _apply_scope(ReportDelivery.objects.select_related("job", "job__organization", "job__data_center"), scope, organization_field="job__organization", data_center_field="job__data_center")
    runs_qs = _apply_scope(ReportScheduleRun.objects.select_related("schedule", "schedule__organization", "schedule__data_center"), scope, organization_field="schedule__organization", data_center_field="schedule__data_center")

    job_rows = list(jobs_qs.filter(status=ReportJobStatus.FAILED).order_by("-failed_at", "-updated_at")[:RECENT_FAILURE_LIMIT])
    delivery_rows = list(deliveries_qs.filter(status=ReportDeliveryStatus.FAILED).order_by("-failed_at", "-updated_at")[:RECENT_FAILURE_LIMIT])
    run_rows = list(runs_qs.filter(status=ReportScheduleRunStatus.FAILED).order_by("-completed_at", "-updated_at")[:RECENT_FAILURE_LIMIT])

    failures = []
    for job in job_rows:
        failures.append(
            {
                "type": "JOB",
                "id": str(job.id),
                "report_name": _job_display_name(job),
                "channel": None,
                "recipient": None,
                "error_code": job.error_code or None,
                "error_message": (job.error_message or "").strip() or None,
                "failed_at": _format_datetime(job.failed_at or job.completed_at or job.updated_at, scope.timezone),
                "organization": getattr(job.organization, "name", None),
                "data_center": getattr(job.data_center, "name", None),
            }
        )
    for delivery in delivery_rows:
        failures.append(
            {
                "type": "DELIVERY",
                "id": str(delivery.id),
                "report_name": _job_display_name(delivery.job),
                "channel": delivery.channel,
                "recipient": _mask_recipient(delivery.recipient),
                "error_code": delivery.error_code or None,
                "error_message": (delivery.error_message or "").strip() or None,
                "failed_at": _format_datetime(delivery.failed_at or delivery.updated_at, scope.timezone),
                "organization": getattr(delivery.job.organization, "name", None),
                "data_center": getattr(delivery.job.data_center, "name", None),
            }
        )
    for run in run_rows:
        failures.append(
            {
                "type": "SCHEDULE_RUN",
                "id": str(run.id),
                "report_name": getattr(run.schedule, "name", None),
                "channel": None,
                "recipient": None,
                "error_code": run.status,
                "error_message": (run.error_message or "").strip() or None,
                "failed_at": _format_datetime(run.completed_at or run.updated_at, scope.timezone),
                "organization": getattr(run.schedule.organization, "name", None),
                "data_center": getattr(run.schedule.data_center, "name", None),
            }
        )

    failures = sorted(
        failures,
        key=lambda row: row["failed_at"] or "",
        reverse=True,
    )
    return failures[:RECENT_FAILURE_LIMIT]


def _frequent_templates(scope: DashboardScope) -> list[dict]:
    jobs_qs = _apply_scope(ReportJob.objects.select_related("template", "definition"), scope, organization_field="organization", data_center_field="data_center")
    rows = (
        jobs_qs.filter(created_at__gte=scope.start_at, created_at__lte=scope.end_at)
        .values("template_id", "template__name", "template__code", "template__definition__code", "template__definition__name")
        .annotate(
            execution_count=Count("id"),
            successful_count=Count("id", filter=Q(status=ReportJobStatus.COMPLETED)),
            failed_count=Count("id", filter=Q(status=ReportJobStatus.FAILED)),
            last_used_at=Max("created_at"),
        )
        .order_by("-execution_count", "-last_used_at")[:FREQUENT_TEMPLATE_LIMIT]
    )
    results = []
    for row in rows:
        results.append(
            {
                "template_id": str(row["template_id"]) if row["template_id"] else None,
                "name": row["template__name"] or "Legacy / Custom",
                "definition": row["template__definition__code"] or "LEGACY_CUSTOM",
                "execution_count": int(row["execution_count"] or 0),
                "successful_count": int(row["successful_count"] or 0),
                "failed_count": int(row["failed_count"] or 0),
                "last_used_at": _format_datetime(row["last_used_at"], scope.timezone),
            }
        )
    return results


def _schedule_health(scope: DashboardScope) -> list[dict]:
    schedules_qs = _apply_scope(
        ReportSchedule.objects.select_related("template", "template__definition", "last_job"),
        scope,
        organization_field="organization",
        data_center_field="data_center",
    )
    rows = (
        schedules_qs.filter(status=ReportScheduleStatus.ACTIVE)
        .annotate(
            failed_run_count=Count("runs", filter=Q(runs__status=ReportScheduleRunStatus.FAILED, runs__created_at__gte=scope.start_at, runs__created_at__lte=scope.end_at), distinct=True),
            total_failed_delivery_count=Count(
                "jobs__deliveries",
                filter=Q(jobs__deliveries__status=ReportDeliveryStatus.FAILED, jobs__deliveries__created_at__gte=scope.start_at, jobs__deliveries__created_at__lte=scope.end_at),
                distinct=True,
            ),
            last_failed_run_at=Max("runs__completed_at", filter=Q(runs__status=ReportScheduleRunStatus.FAILED)),
        )
        .order_by("-failed_run_count", "next_run_at", "created_at")[:SCHEDULE_HEALTH_LIMIT]
    )
    results = []
    for schedule in rows:
        health_status = "HEALTHY"
        if schedule.next_run_at is None:
            health_status = "CRITICAL"
        elif schedule.failed_run_count and schedule.failed_run_count >= 2:
            health_status = "CRITICAL"
        elif schedule.last_job and schedule.last_job.status == ReportJobStatus.FAILED:
            health_status = "WARNING"
        elif schedule.failed_run_count or schedule.total_failed_delivery_count:
            health_status = "WARNING"
        if schedule.next_run_at and schedule.next_run_at < dj_timezone.now():
            health_status = "WARNING" if health_status == "HEALTHY" else health_status

        if health_status == "HEALTHY" and not schedule.last_job and not schedule.failed_run_count and not schedule.total_failed_delivery_count:
            continue

        results.append(
            {
                "schedule_id": str(schedule.id),
                "schedule_name": schedule.name,
                "template_name": getattr(schedule.template, "name", None),
                "definition_code": getattr(schedule.template.definition, "code", None) if getattr(schedule.template, "definition_id", None) else None,
                "failure_count": int(schedule.failed_run_count or 0),
                "delivery_failure_count": int(schedule.total_failed_delivery_count or 0),
                "last_failure_at": _format_datetime(schedule.last_failed_run_at, scope.timezone),
                "last_error": (schedule.last_error_message or "").strip() or None,
                "next_run_at": _format_datetime(schedule.next_run_at, scope.timezone),
                "health_status": health_status,
            }
        )
    return results


def _operational_health(scope: DashboardScope, summary: dict) -> dict:
    jobs_qs = _apply_scope(ReportJob.objects.all(), scope, organization_field="organization", data_center_field="data_center")
    deliveries_qs = _apply_scope(ReportDelivery.objects.all(), scope, organization_field="job__organization", data_center_field="job__data_center")
    runs_qs = _apply_scope(ReportScheduleRun.objects.all(), scope, organization_field="schedule__organization", data_center_field="schedule__data_center")

    threshold = dj_timezone.now() - timedelta(minutes=QUEUE_AGE_THRESHOLD_MINUTES)
    oldest_queued_job_at = jobs_qs.filter(status=ReportJobStatus.QUEUED).aggregate(oldest=Min("queued_at"))["oldest"]
    oldest_queued_delivery_at = deliveries_qs.filter(status=ReportDeliveryStatus.QUEUED).aggregate(oldest=Min("queued_at"))["oldest"]

    return {
        "scheduler_last_activity_at": _format_datetime(runs_qs.aggregate(last_activity=Max("updated_at"))["last_activity"], scope.timezone),
        "oldest_queued_job_at": _format_datetime(oldest_queued_job_at, scope.timezone),
        "oldest_queued_job_age_seconds": int((dj_timezone.now() - oldest_queued_job_at).total_seconds()) if oldest_queued_job_at else None,
        "queued_jobs_over_threshold": int(jobs_qs.filter(status=ReportJobStatus.QUEUED, queued_at__lte=threshold).count()),
        "oldest_queued_delivery_at": _format_datetime(oldest_queued_delivery_at, scope.timezone),
        "oldest_queued_delivery_age_seconds": int((dj_timezone.now() - oldest_queued_delivery_at).total_seconds()) if oldest_queued_delivery_at else None,
        "queued_deliveries_over_threshold": int(deliveries_qs.filter(status=ReportDeliveryStatus.QUEUED, queued_at__lte=threshold).count()),
        "total_artifact_storage_size_bytes": int(summary.get("total_artifact_size_bytes") or 0),
        "retention_cleanup_last_result": None,
    }


def get_reporting_dashboard(*, user, organization=None, data_center=None, start_at=None, end_at=None, timezone_name="Asia/Dhaka", request=None):
    from time import perf_counter

    started = perf_counter()
    tz = _resolve_timezone(timezone_name)
    scope = _resolve_scope(user, organization=organization, data_center=data_center, timezone_name=timezone_name)

    if start_at is not None:
        scope_start_at = _ensure_aware(start_at, tz)
    else:
        scope_start_at = scope.start_at
    if end_at is not None:
        scope_end_at = _ensure_aware(end_at, tz)
    else:
        scope_end_at = scope.end_at

    if scope_start_at >= scope_end_at:
        raise ValidationError({"start_at": "Start time must be before end time."})
    if (scope_end_at - scope_start_at) > timedelta(days=MAX_DASHBOARD_RANGE_DAYS):
        raise ValidationError({"end_at": f"Dashboard range must not exceed {MAX_DASHBOARD_RANGE_DAYS} days."})

    scope = DashboardScope(
        user=scope.user,
        organization=scope.organization,
        data_center=scope.data_center,
        organization_ids=scope.organization_ids,
        data_center_ids=scope.data_center_ids,
        global_access=scope.global_access,
        timezone=tz,
        start_at=scope_start_at,
        end_at=scope_end_at,
        today_start=scope.today_start,
        today_end=scope.today_end,
    )

    log_report_event(
        logger,
        "Dashboard requested",
        organization=scope.organization,
        data_center=scope.data_center,
        request=request,
    )

    summary = _summary_for_scope(scope)
    payload = {
        "range": {
            "start_at": scope.start_at.isoformat(),
            "end_at": scope.end_at.isoformat(),
            "timezone": tz.key,
        },
        "summary": summary,
        "generation_trend": _generation_trend(scope),
        "by_definition": _reports_by_definition(scope),
        "by_format": _reports_by_format(scope),
        "delivery_summary": _delivery_summary(scope),
        "recent_jobs": _recent_jobs(scope),
        "upcoming_schedules": _upcoming_schedules(scope),
        "recent_failures": _recent_failures(scope),
        "frequent_templates": _frequent_templates(scope),
        "schedule_health": _schedule_health(scope),
        "operational_health": _operational_health(scope, summary),
    }
    elapsed_ms = round((perf_counter() - started) * 1000)
    log_report_event(
        logger,
        "Dashboard completed",
        organization=scope.organization,
        data_center=scope.data_center,
        execution_time_ms=elapsed_ms,
        request=request,
    )
    log_report_metric(logger, "report_dashboard_response", value=elapsed_ms, organization=scope.organization, data_center=scope.data_center, request=request)
    return payload
