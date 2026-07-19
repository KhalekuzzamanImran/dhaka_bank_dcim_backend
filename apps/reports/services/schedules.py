from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.audit import write_audit
from apps.accounts.models import User
from apps.notifications.services.sms import send_sms_message

from ..models import ReportJob, ReportJobStatus, ReportSchedule
from .generator import generate_report_job

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedSchedule:
    schedule_id: str
    window_start: str
    window_end: str


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
    lines = [
        f"Scheduled report: {schedule.name}",
        f"Report type: {schedule.report_type_label}",
        f"Frequency: {schedule.get_frequency_display()} at {schedule.delivery_time.strftime('%I:%M %p')}",
        f"Requested format: {schedule.get_output_format_display()}",
    ]
    if window_start or window_end:
        lines.append(f"Window: {window_start or '--'} to {window_end or '--'}")
    lines.append("")
    lines.append("The generated report is attached to this email.")
    return "\n".join(lines)


def _send_report_email(schedule: ReportSchedule, report_job: ReportJob):
    if not report_job.file:
        raise ValueError("Generated report file is missing.")

    recipients = schedule.normalize_recipients()
    if not recipients:
        raise ValueError("Report schedule does not have any recipients.")

    file_name = os.path.basename(report_job.file.name)
    with report_job.file.open("rb") as handle:
        attachment = handle.read()

    message = EmailMessage(
        subject=f"{schedule.name} - {schedule.report_type_label}",
        body=_build_email_body(schedule, report_job),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=recipients,
    )
    message.attach(file_name, attachment, "text/csv")
    message.send(fail_silently=False)


def _send_report_sms(schedule: ReportSchedule, report_job: ReportJob):
    recipients = schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []
    if not schedule.send_sms:
        return []
    if not recipients:
        raise ValueError("SMS delivery is enabled but no SMS recipients were configured.")

    message = (
        f"Scheduled report ready: {schedule.name}. "
        f"Type: {schedule.report_type_label}. "
        f"Window: {report_job.parameters.get('date_from', '--')} to {report_job.parameters.get('date_to', '--')}."
    )
    return [send_sms_message(str(phone), message) for phone in recipients]


def claim_due_report_schedules(limit: int = 100) -> list[ClaimedSchedule]:
    now = timezone.now()
    claimed: list[ClaimedSchedule] = []
    with transaction.atomic():
        due_schedules = (
            ReportSchedule.objects.select_for_update(skip_locked=True)
            .filter(is_active=True, next_run_at__lte=now)
            .order_by("next_run_at", "created_at")[:limit]
        )
        for schedule in due_schedules:
            window_start, window_end = schedule.calculate_execution_window(reference_time=now)
            schedule.last_run_at = window_end
            schedule.next_run_at = schedule.calculate_next_run_at(reference_time=now)
            schedule.last_delivery_status = "PENDING"
            schedule.last_error_message = ""
            schedule.save(update_fields=["last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])
            claimed.append(
                ClaimedSchedule(
                    schedule_id=str(schedule.pk),
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                )
            )
    return claimed


def execute_report_schedule(schedule_id: str, *, window_start: str | None = None, window_end: str | None = None):
    schedule = ReportSchedule.objects.select_related("organization", "data_center", "created_by", "last_job").filter(pk=schedule_id).first()
    if not schedule:
        raise ValueError(f"Report schedule {schedule_id} does not exist.")

    if not schedule.is_active:
        logger.info("Skipping inactive report schedule schedule=%s", schedule.pk)
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
    if window_end_dt is None:
        window_end_dt = timezone.now()
    if window_start_dt is None:
        window_start_dt, _ = schedule.calculate_execution_window(reference_time=window_end_dt)
    schedule.last_run_at = window_end_dt
    schedule.next_run_at = schedule.calculate_next_run_at(reference_time=window_end_dt)

    parameters = {
        **(schedule.parameters if isinstance(schedule.parameters, dict) else {}),
        "report_type": schedule.report_type,
        "output_format": schedule.output_format,
        "date_from": window_start_dt.isoformat(),
        "date_to": window_end_dt.isoformat(),
        "schedule_id": str(schedule.pk),
        "schedule_name": schedule.name,
        "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
        "requested_format": schedule.output_format,
        "attach_raw_data": schedule.attach_raw_data,
    }

    job = ReportJob.objects.create(
        organization=schedule.organization,
        data_center=schedule.data_center,
        requested_by=requested_by,
        status=ReportJobStatus.PENDING,
        parameters=parameters,
    )

    generated_job = generate_report_job(job.id)
    schedule.last_job = generated_job

    if generated_job.status != ReportJobStatus.COMPLETED or not generated_job.file:
        schedule.last_delivery_status = "FAILED"
        schedule.last_error_message = generated_job.error_message or "Scheduled report generation failed."
        schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])
        _safe_write_audit(
            "REPORT_GENERATION_FAILED",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=requested_by,
            message=schedule.last_error_message,
        )
        return schedule

    try:
        if email_recipients:
            _send_report_email(schedule, generated_job)
        _send_report_sms(schedule, generated_job)
    except Exception as exc:
        schedule.last_delivery_status = "FAILED"
        schedule.last_error_message = str(exc)
        schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])
        _safe_write_audit(
            "REPORT_GENERATION_FAILED",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=requested_by,
            message=str(exc),
        )
        raise

    schedule.last_sent_at = timezone.now()
    schedule.last_delivery_status = "SENT"
    schedule.last_error_message = ""
    schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_sent_at", "last_delivery_status", "last_error_message", "updated_at"])
    _safe_write_audit(
        "REPORT_GENERATED",
        "ReportSchedule",
        schedule.pk,
        organization=schedule.organization,
        actor=requested_by,
        message=f"Scheduled report delivered for {schedule.report_type_label}",
    )
    return schedule
