from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from copy import deepcopy

from django.db import IntegrityError, transaction

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.audit import write_audit
from apps.accounts.models import User
from apps.notifications.models import NotificationChannel
from .observability import log_report_event, log_report_metric

from ..models import (
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleRun,
    ReportScheduleRunStatus,
    ReportScheduleStatus,
)
from .deliveries import report_delivery_summary
from .definitions import get_active_definition_by_code
from .factory import create_report_job
from .execution import generate_report_job

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedSchedule:
    schedule_id: str
    window_start: str
    window_end: str
    scheduled_for: str | None = None


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write report schedule audit log.", exc_info=True)
        return None


def _fallback_requested_by(schedule: ReportSchedule):
    if schedule.created_by_id:
        return schedule.created_by

    active_user = (
        User.objects.filter(
            is_active=True,
            data_center_roles__organization_id=schedule.organization_id,
            data_center_roles__is_active=True,
        )
        .order_by("date_joined")
        .distinct()
        .first()
    )
    if active_user:
        return active_user

    return User.objects.filter(is_active=True).order_by("date_joined").first()


def _build_email_body(schedule: ReportSchedule, report_job: ReportJob) -> str:
    window_start = report_job.parameters.get("date_from") if isinstance(report_job.parameters, dict) else None
    window_end = report_job.parameters.get("date_to") if isinstance(report_job.parameters, dict) else None
    report_name = getattr(getattr(schedule, "template", None), "definition", None)
    report_name = getattr(report_name, "name", None) or getattr(schedule.template, "name", None) or schedule.name
    lines = [
        f"Scheduled report: {schedule.name}",
        f"Report type: {report_name}",
        f"Frequency: {schedule.get_frequency_display()} at {schedule.delivery_time.strftime('%I:%M %p')}",
        f"Requested format: {schedule.primary_format or getattr(schedule.template, 'primary_format', None) or 'CSV'}",
    ]
    if window_start or window_end:
        lines.append(f"Window: {window_start or '--'} to {window_end or '--'}")
    lines.append("")
    lines.append("The generated report is attached to this email.")
    return "\n".join(lines)


def _send_report_email(schedule: ReportSchedule, report_job: ReportJob, recipient: str | None = None):
    recipients = [recipient] if recipient else [
        row.email_address
        for row in schedule.structured_recipients.filter(channel=ReportRecipientChannel.EMAIL, is_active=True).order_by("created_at", "pk")
        if row.email_address
    ]
    if not recipients:
        raise ValueError("Report schedule does not have any recipients.")

    artifact = report_job.artifacts.order_by("created_at", "pk").first()
    if not artifact or not artifact.file:
        raise ValueError("Generated report artifact is missing.")
    file_name = os.path.basename(artifact.file.name)
    with artifact.file.open("rb") as handle:
        attachment = handle.read()

    message = EmailMessage(
        subject=f"{schedule.name} - {getattr(getattr(schedule, 'template', None), 'definition', None).name if getattr(getattr(schedule, 'template', None), 'definition', None) else getattr(schedule.template, 'name', None) or schedule.name}",
        body=_build_email_body(schedule, report_job),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=recipients,
    )
    message.attach(file_name, attachment, "text/csv")
    message.send(fail_silently=False)


def _build_report_sms_message(schedule: ReportSchedule, report_job: ReportJob):
    report_name = getattr(getattr(schedule, "template", None), "definition", None)
    report_name = getattr(report_name, "name", None) or getattr(schedule.template, "name", None) or schedule.name
    return (
        f"Scheduled report ready: {schedule.name}. "
        f"Type: {report_name}. "
        f"Window: {report_job.parameters.get('date_from', '--')} to {report_job.parameters.get('date_to', '--')}."
    )


def _create_schedule_run(
    schedule: ReportSchedule,
    *,
    requested_by,
    window_start,
    window_end,
    scheduled_for,
    trigger_source: str,
    parameters: dict,
):
    template = getattr(schedule, "template", None)
    template_definition = getattr(template, "definition", None)
    snapshot = {
        "schedule_id": str(schedule.pk),
        "schedule_name": schedule.name,
        "definition_code": template_definition.code if template_definition else None,
        "frequency": schedule.frequency,
        "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
        "primary_format": schedule.primary_format,
        "attachment_formats": list(schedule.attachment_formats or []),
        "recipients": [
            {
                "channel": row.channel,
                "display_name": row.display_name,
                "email_address": row.email_address,
                "phone_number": row.phone_number,
                "is_active": row.is_active,
            }
            for row in schedule.structured_recipients.filter(is_active=True).order_by("created_at", "pk")
        ],
        "attach_raw_data": schedule.attach_raw_data,
        "parameters": parameters,
        "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
        "trigger_source": trigger_source,
    }
    run = ReportScheduleRun.objects.create(
        schedule=schedule,
        organization=schedule.organization,
        requested_by=requested_by,
        window_start=window_start,
        window_end=window_end,
        scheduled_for=scheduled_for,
        status=ReportScheduleRunStatus.PENDING,
        queued_at=timezone.now(),
        trigger_source=trigger_source,
        snapshot=snapshot,
    )

    return run


def _queue_report_sms_notifications(schedule: ReportSchedule, report_job: ReportJob, run: ReportScheduleRun):
    if not schedule.send_sms:
        return []
    recipients = [
        row.phone_number
        for row in schedule.structured_recipients.filter(channel=ReportRecipientChannel.SMS, is_active=True).order_by("created_at", "pk")
        if row.phone_number
    ]
    if not recipients:
        raise ValueError("SMS delivery is enabled but no SMS recipients were configured.")

    queued_notifications = []
    message = _build_report_sms_message(schedule, report_job)
    template_definition = getattr(getattr(schedule, "template", None), "definition", None)
    report_schedule_id = str(schedule.pk)
    report_job_id = str(report_job.pk)
    now = timezone.now()

    for phone in recipients:
        normalized_phone = str(phone).strip()
        if not normalized_phone:
            continue
        notification, created = Notification.objects.get_or_create(
            organization=schedule.organization,
            dedupe_key=f"report_schedule:{schedule.pk}:{report_job_id}:sms:{normalized_phone}",
            defaults={
                "recipient": None,
                "subject": f"{schedule.name} - SMS delivery",
                "message": message,
                "metadata": {
                    "report_schedule_id": report_schedule_id,
                    "report_job_id": report_job_id,
                    "report_schedule_name": schedule.name,
                    "definition_code": template_definition.code if template_definition else None,
                    "phone": normalized_phone,
                },
            },
        )
        if not created:
            metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
            metadata = dict(metadata)
            metadata.update(
                {
                    "report_schedule_id": report_schedule_id,
                    "report_job_id": report_job_id,
                    "report_schedule_name": schedule.name,
                    "definition_code": template_definition.code if template_definition else None,
                    "phone": normalized_phone,
                }
            )
            notification.message = message
            notification.metadata = metadata
            notification.save(update_fields=["message", "metadata", "updated_at"])
        delivery = NotificationDelivery.objects.filter(
            notification__organization=schedule.organization,
            notification=notification,
            channel=NotificationChannel.SMS,
        ).first()
        if not delivery:
            delivery = NotificationDelivery.objects.create(
                notification=notification,
                channel=NotificationChannel.SMS,
                status=NotificationStatus.PENDING,
                recipient_address=normalized_phone,
                attempt_count=0,
                max_attempts=3,
                queued_at=now,
                metadata={
                    "report_schedule_id": report_schedule_id,
                    "report_job_id": report_job_id,
                    "report_schedule_name": schedule.name,
                    "definition_code": template_definition.code if template_definition else None,
                    "phone": normalized_phone,
                    "channel": NotificationChannel.SMS,
                },
            )
        if not delivery.recipient_address:
            delivery.recipient_address = normalized_phone
            delivery.save(update_fields=["recipient_address", "updated_at"])
        delivery.status = NotificationStatus.DELIVERING
        delivery.delivering_at = timezone.now()
        delivery.attempt_count = delivery.attempt_count + 1
        delivery.save(update_fields=["status", "delivering_at", "attempt_count", "updated_at"])
        try:
            deliver_notification_delivery(delivery)
        except Exception as exc:
            delivery = NotificationDelivery.objects.filter(pk=delivery.pk).first() or delivery
            delivery.status = NotificationStatus.FAILED
            delivery.failed_at = timezone.now()
            delivery.error_message = str(exc)
            delivery.save(update_fields=["status", "failed_at", "error_message", "updated_at"])
            raise

        queued_notifications.append(notification)

    return queued_notifications


def claim_due_report_schedules(limit: int = 100) -> list[ClaimedSchedule]:
    now = timezone.now()
    claimed: list[ClaimedSchedule] = []
    with transaction.atomic():
        due_schedules = (
            ReportSchedule.objects.select_for_update(skip_locked=True)
            .filter(status=ReportScheduleStatus.ACTIVE, next_run_at__lte=now)
            .order_by("next_run_at", "created_at")[:limit]
        )
        for schedule in due_schedules:
            scheduled_for = schedule.next_run_at
            window_start, window_end = schedule.calculate_execution_window(reference_time=scheduled_for)
            schedule.last_run_at = scheduled_for
            schedule.next_run_at = schedule.calculate_next_run_at(reference_time=scheduled_for)
            schedule.last_delivery_status = "PENDING"
            schedule.last_error_message = ""
            schedule.save(update_fields=["last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])
            queue_latency_ms = max(0, int((now - scheduled_for).total_seconds() * 1000)) if scheduled_for else 0
            log_report_event(logger, "Report schedule claimed", schedule=schedule, trigger_source="SCHEDULED", queue_latency_ms=queue_latency_ms)
            log_report_metric(logger, "report_scheduler_latency_ms", value=queue_latency_ms, schedule=schedule, trigger_source="SCHEDULED")
            claimed.append(
                ClaimedSchedule(
                    schedule_id=str(schedule.pk),
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                    scheduled_for=scheduled_for.isoformat() if scheduled_for else None,
                )
            )
    return claimed


def execute_report_schedule(
    schedule_id: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    scheduled_for: str | None = None,
    trigger_source: str = "SCHEDULED",
):
    schedule = (
        ReportSchedule.objects.select_related(
            "organization",
            "data_center",
            "created_by",
            "last_job",
            "template",
            "template__definition",
        )
        .filter(pk=schedule_id)
        .first()
    )
    if not schedule:
        raise ValueError(f"Report schedule {schedule_id} does not exist.")

    if schedule.status != ReportScheduleStatus.ACTIVE:
        log_report_event(logger, "Skipping inactive report schedule", schedule=schedule, trigger_source=trigger_source)
        return schedule

    email_recipients = schedule.normalize_recipients()
    sms_recipients = schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []
    if not email_recipients and not (schedule.send_sms and sms_recipients):
        message = "Report schedule has no email or SMS recipients configured."
        ReportSchedule.objects.filter(pk=schedule.pk).update(
            last_delivery_status="FAILED",
            last_error_message=message,
            updated_at=timezone.now(),
        )
        schedule.last_delivery_status = "FAILED"
        schedule.last_error_message = message
        raise ValueError(message)

    requested_by = _fallback_requested_by(schedule)
    if not requested_by:
        raise ValueError("Unable to resolve a requesting user for report schedule execution.")

    window_start_dt = parse_datetime(window_start) if window_start else None
    window_end_dt = parse_datetime(window_end) if window_end else None
    scheduled_for_dt = parse_datetime(scheduled_for) if scheduled_for else None
    if scheduled_for_dt is None and trigger_source == "SCHEDULED":
        scheduled_for_dt = schedule.next_run_at
    if window_end_dt is None:
        window_end_dt = scheduled_for_dt or timezone.now()
    if window_start_dt is None:
        window_start_dt, _ = schedule.calculate_execution_window(reference_time=scheduled_for_dt or window_end_dt)

    template = schedule.template if schedule.template_id else None
    definition = template.definition if template and template.definition_id else None
    if definition is None and template is not None:
        raise ValueError("Report schedule template is missing a report definition.")

    template_defaults = deepcopy(template.default_parameters) if template and isinstance(template.default_parameters, dict) else {}
    schedule_overrides = deepcopy(schedule.parameter_overrides) if isinstance(schedule.parameter_overrides, dict) else {}
    runtime_parameters = {
        "definition_code": getattr(definition, "code", None),
        "schedule_id": str(schedule.pk),
        "schedule_name": schedule.name,
        "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
        "primary_format": schedule.primary_format or getattr(template, "primary_format", None),
        "attachment_formats": schedule.attachment_formats,
        "attach_raw_data": schedule.attach_raw_data,
    }

    parameters = {}
    if template:
        parameters.update(template_defaults)
        parameters.update(schedule_overrides)
    else:
        configured_parameters = schedule.parameters if isinstance(schedule.parameters, dict) else {}
        parameters.update(configured_parameters)
    parameters.update(runtime_parameters)

    has_date_from = parameters.get("date_from") not in (None, "") or parameters.get("start_date") not in (None, "")
    has_date_to = parameters.get("date_to") not in (None, "") or parameters.get("end_date") not in (None, "")
    if not has_date_from and not has_date_to:
        parameters["date_from"] = window_start_dt.isoformat()
        parameters["date_to"] = window_end_dt.isoformat()

    parameters_snapshot = deepcopy(parameters)
    template_snapshot = {}
    output_config_snapshot = {
        "definition_code": getattr(definition, "code", None),
        "primary_format": template.primary_format if template and template.primary_format else schedule.primary_format,
        "attachment_formats": deepcopy(template.attachment_formats if template and isinstance(template.attachment_formats, list) else []),
        "attach_raw_data": schedule.attach_raw_data,
    }
    scope_snapshot = {
        "organization_id": str(schedule.organization_id) if schedule.organization_id else None,
        "organization_name": getattr(schedule.organization, "name", None),
        "data_center_id": str(schedule.data_center_id) if schedule.data_center_id else None,
        "data_center_name": getattr(schedule.data_center, "name", None),
    }
    recipient_snapshot = {
        "email_recipients": [
            {
                "channel": row.channel,
                "display_name": row.display_name,
                "email_address": row.email_address,
                "phone_number": row.phone_number,
                "is_active": row.is_active,
            }
            for row in schedule.structured_recipients.filter(is_active=True).order_by("created_at", "pk")
            if row.channel == ReportRecipientChannel.EMAIL
        ],
        "sms_recipients": [
            {
                "channel": row.channel,
                "display_name": row.display_name,
                "email_address": row.email_address,
                "phone_number": row.phone_number,
                "is_active": row.is_active,
            }
            for row in schedule.structured_recipients.filter(is_active=True).order_by("created_at", "pk")
            if row.channel == ReportRecipientChannel.SMS
        ],
        "send_sms": schedule.send_sms,
    }
    if template:
        template_snapshot = {
            "id": str(template.pk),
            "code": template.code,
            "name": template.name,
            "version": template.version,
            "definition_id": str(template.definition_id) if template.definition_id else None,
            "definition_code": getattr(template.definition, "code", None),
            "default_parameters": deepcopy(template.default_parameters if isinstance(template.default_parameters, dict) else {}),
            "config": deepcopy(template.config if isinstance(template.config, dict) else {}),
            "primary_format": template.primary_format,
            "attachment_formats": deepcopy(template.attachment_formats if isinstance(template.attachment_formats, list) else []),
            "include_charts": template.include_charts,
            "include_raw_data": template.include_raw_data,
            "is_active": template.is_active,
        }

    with transaction.atomic():
        schedule = ReportSchedule.objects.select_for_update().filter(pk=schedule.pk).first()
        if not schedule:
            raise ValueError(f"Report schedule {schedule_id} does not exist.")

        factory_result = create_report_job(
            definition=definition,
            organization=schedule.organization,
            actor=None,
            data_center=schedule.data_center,
            template=schedule.template if schedule.template_id else None,
            schedule=schedule,
            trigger_source=trigger_source,
            requested_by=requested_by,
            parameters=parameters,
            runtime_parameters={
                "delivery_channels": ["EMAIL"] + (["SMS"] if schedule.send_sms else []),
                "primary_format": schedule.primary_format or schedule.output_format,
                "attachment_formats": schedule.attachment_formats,
            },
            source_event={},
            scheduled_for=scheduled_for_dt,
            window_start=window_start_dt,
            window_end=window_end_dt,
            queue_job=False,
            compatibility_mode=True,
        )
        job = factory_result.job
        run = factory_result.schedule_run

        schedule.last_run_at = scheduled_for_dt or window_end_dt
        schedule.next_run_at = schedule.calculate_next_run_at(reference_time=scheduled_for_dt or window_end_dt)
        schedule.last_delivery_status = "PENDING"
        schedule.last_error_message = ""
        schedule.last_job = job
        schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])

        if factory_result.duplicate:
            log_report_metric(logger, "report_schedule_duplicated", schedule=schedule, trigger_source=trigger_source)
            return schedule

    try:
        completed_job = generate_report_job(job.id)
        run.job = completed_job
        run.started_at = completed_job.started_at or timezone.now()
        run.completed_at = completed_job.completed_at or timezone.now()
        if completed_job.status != ReportJobStatus.COMPLETED or not completed_job.artifacts.exists():
            run.status = ReportScheduleRunStatus.FAILED
            run.error_message = completed_job.error_message or "Scheduled report generation failed."
            run.save(update_fields=["job", "started_at", "completed_at", "status", "error_message", "updated_at"])
            schedule.last_delivery_status = "FAILED"
            schedule.last_error_message = run.error_message
            schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])
            _safe_write_audit(
                "REPORT_GENERATION_FAILED",
                "ReportSchedule",
                schedule.pk,
                organization=schedule.organization,
                actor=requested_by,
                message=schedule.last_error_message,
            )
            log_report_event(logger, "Scheduled report generation failed", schedule=schedule, job=completed_job, trigger_source=trigger_source)
            return schedule

        run.status = ReportScheduleRunStatus.COMPLETED
        run.error_message = ""
        run.save(update_fields=["job", "started_at", "completed_at", "status", "error_message", "updated_at"])

        delivery_summary = report_delivery_summary(completed_job)
        schedule.last_sent_at = timezone.now() if delivery_summary in {"SENT", "PARTIAL", "FAILED"} else schedule.last_sent_at
        schedule.last_delivery_status = delivery_summary
        schedule.last_error_message = ""
        schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_sent_at", "last_delivery_status", "last_error_message", "updated_at"])
        _safe_write_audit(
            "REPORT_GENERATED",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=requested_by,
            message=f"Scheduled report delivered for {getattr(getattr(schedule, 'template', None), 'definition', None).name if getattr(getattr(schedule, 'template', None), 'definition', None) else getattr(schedule.template, 'name', None) or schedule.name}",
        )
        log_report_event(
            logger,
            "Scheduled report execution finished",
            schedule=schedule,
            job=completed_job,
            trigger_source=trigger_source,
            execution_time_ms=0,
        )
        log_report_metric(logger, "report_schedule_runs_completed", schedule=schedule, trigger_source=trigger_source)
        return schedule
    except Exception:
        raise
