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
from apps.notifications.models import Notification, NotificationChannel, NotificationDelivery, NotificationStatus
from apps.notifications.services import deliver_notification_delivery, queue_notification_delivery

from ..models import (
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleDelivery,
    ReportScheduleDeliveryStatus,
    ReportScheduleRun,
    ReportScheduleRunStatus,
)
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


def _send_report_email(schedule: ReportSchedule, report_job: ReportJob, recipient: str | None = None):
    if not report_job.file:
        raise ValueError("Generated report file is missing.")

    recipients = [recipient] if recipient else schedule.normalize_recipients()
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


def _build_report_sms_message(schedule: ReportSchedule, report_job: ReportJob):
    return (
        f"Scheduled report ready: {schedule.name}. "
        f"Type: {schedule.report_type_label}. "
        f"Window: {report_job.parameters.get('date_from', '--')} to {report_job.parameters.get('date_to', '--')}."
    )


def _create_schedule_run(
    schedule: ReportSchedule,
    *,
    requested_by,
    window_start,
    window_end,
    trigger_source: str,
    parameters: dict,
):
    snapshot = {
        "schedule_id": str(schedule.pk),
        "schedule_name": schedule.name,
        "report_type": schedule.report_type,
        "frequency": schedule.frequency,
        "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
        "output_format": schedule.output_format,
        "recipients": schedule.normalize_recipients(),
        "sms_recipients": list(schedule.sms_recipients or []) if isinstance(schedule.sms_recipients, list) else [],
        "attach_raw_data": schedule.attach_raw_data,
        "parameters": parameters,
        "trigger_source": trigger_source,
    }
    run = ReportScheduleRun.objects.create(
        schedule=schedule,
        organization=schedule.organization,
        requested_by=requested_by,
        window_start=window_start,
        window_end=window_end,
        status=ReportScheduleRunStatus.PENDING,
        queued_at=timezone.now(),
        trigger_source=trigger_source,
        snapshot=snapshot,
    )

    now = timezone.now()
    ReportScheduleDelivery.objects.create(
        run=run,
        channel=NotificationChannel.WEB,
        status=ReportScheduleDeliveryStatus.SENT,
        recipient_address="",
        queued_at=now,
        delivering_at=now,
        sent_at=now,
        metadata={"channel": NotificationChannel.WEB},
    )
    for email in schedule.normalize_recipients():
        ReportScheduleDelivery.objects.create(
            run=run,
            channel=NotificationChannel.EMAIL,
            status=ReportScheduleDeliveryStatus.PENDING,
            recipient_address=email,
            queued_at=now,
            metadata={"channel": NotificationChannel.EMAIL, "recipient_address": email},
        )
    if schedule.send_sms:
        for phone in schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []:
            normalized_phone = str(phone).strip()
            if not normalized_phone:
                continue
            ReportScheduleDelivery.objects.create(
                run=run,
                channel=NotificationChannel.SMS,
                status=ReportScheduleDeliveryStatus.PENDING,
                recipient_address=normalized_phone,
                queued_at=now,
                metadata={"channel": NotificationChannel.SMS, "recipient_address": normalized_phone},
            )
    return run


def _queue_report_sms_notifications(schedule: ReportSchedule, report_job: ReportJob, run: ReportScheduleRun):
    if not schedule.send_sms:
        return []
    recipients = schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []
    if not recipients:
        raise ValueError("SMS delivery is enabled but no SMS recipients were configured.")

    queued_notifications = []
    message = _build_report_sms_message(schedule, report_job)
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
                    "report_type": schedule.report_type,
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
                    "report_type": schedule.report_type,
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
                    "report_type": schedule.report_type,
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

        schedule_delivery = run.deliveries.filter(channel=NotificationChannel.SMS, recipient_address=normalized_phone).first()
        if schedule_delivery:
            schedule_delivery.status = ReportScheduleDeliveryStatus.DELIVERING
            schedule_delivery.delivering_at = now
            schedule_delivery.provider_message_id = str(notification.pk)
            schedule_delivery.provider_response = {"notification_id": str(notification.pk), "queued": True}
            schedule_delivery.save(update_fields=["status", "delivering_at", "provider_message_id", "provider_response", "updated_at"])

        queued_notifications.append(notification)

    return queued_notifications


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


def execute_report_schedule(
    schedule_id: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    trigger_source: str = "SCHEDULED",
):
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

    configured_parameters = schedule.parameters if isinstance(schedule.parameters, dict) else {}
    parameters = {
        **configured_parameters,
        "report_type": schedule.report_type,
        "output_format": schedule.output_format,
        "schedule_id": str(schedule.pk),
        "schedule_name": schedule.name,
        "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
        "requested_format": schedule.output_format,
        "attach_raw_data": schedule.attach_raw_data,
    }

    has_date_from = parameters.get("date_from") not in (None, "") or parameters.get("start_date") not in (None, "")
    has_date_to = parameters.get("date_to") not in (None, "") or parameters.get("end_date") not in (None, "")
    if not has_date_from and not has_date_to:
        parameters["date_from"] = window_start_dt.isoformat()
        parameters["date_to"] = window_end_dt.isoformat()

    with transaction.atomic():
        schedule = ReportSchedule.objects.select_for_update().filter(pk=schedule.pk).first()
        if not schedule:
            raise ValueError(f"Report schedule {schedule_id} does not exist.")

        run = _create_schedule_run(
            schedule,
            requested_by=requested_by,
            window_start=window_start_dt,
            window_end=window_end_dt,
            trigger_source=trigger_source,
            parameters=parameters,
        )

        job = ReportJob.objects.create(
            organization=schedule.organization,
            data_center=schedule.data_center,
            requested_by=requested_by,
            status=ReportJobStatus.PENDING,
            parameters=parameters,
        )

        schedule.last_run_at = window_end_dt
        schedule.next_run_at = schedule.calculate_next_run_at(reference_time=window_end_dt)
        schedule.last_delivery_status = "PENDING"
        schedule.last_error_message = ""
        schedule.last_job = job
        schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])

    try:
        generated_job = generate_report_job(job.id)
        run.generated_job = generated_job
        run.started_at = generated_job.started_at or timezone.now()
        run.completed_at = generated_job.completed_at or timezone.now()
        if generated_job.status != ReportJobStatus.COMPLETED or not generated_job.file:
            run.status = ReportScheduleRunStatus.FAILED
            run.error_message = generated_job.error_message or "Scheduled report generation failed."
            run.save(update_fields=["generated_job", "started_at", "completed_at", "status", "error_message", "updated_at"])
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
            return schedule

        email_failed = False
        for recipient in email_recipients:
            delivery = run.deliveries.filter(channel=NotificationChannel.EMAIL, recipient_address=recipient).first()
            if not delivery:
                delivery = ReportScheduleDelivery.objects.create(
                    run=run,
                    channel=NotificationChannel.EMAIL,
                    status=ReportScheduleDeliveryStatus.PENDING,
                    recipient_address=recipient,
                    queued_at=timezone.now(),
                    metadata={"channel": NotificationChannel.EMAIL, "recipient_address": recipient},
                )
            delivery.status = ReportScheduleDeliveryStatus.DELIVERING
            delivery.delivering_at = timezone.now()
            delivery.attempt_count = delivery.attempt_count + 1
            delivery.save(update_fields=["status", "delivering_at", "attempt_count", "updated_at"])
            try:
                _send_report_email(schedule, generated_job, recipient)
                delivery.status = ReportScheduleDeliveryStatus.SENT
                delivery.sent_at = timezone.now()
                delivery.error_message = ""
                delivery.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
            except Exception as exc:
                email_failed = True
                delivery.status = ReportScheduleDeliveryStatus.FAILED
                delivery.failed_at = timezone.now()
                delivery.error_message = str(exc)
                delivery.save(update_fields=["status", "failed_at", "error_message", "updated_at"])

        sms_failed = False
        if schedule.send_sms:
            try:
                _queue_report_sms_notifications(schedule, generated_job, run)
                for recipient in sms_recipients:
                    normalized_phone = str(recipient).strip()
                    if not normalized_phone:
                        continue
                    delivery = run.deliveries.filter(channel=NotificationChannel.SMS, recipient_address=normalized_phone).first()
                    if delivery:
                        delivery.status = ReportScheduleDeliveryStatus.SENT
                        delivery.sent_at = timezone.now()
                        delivery.error_message = ""
                        delivery.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
            except Exception as exc:
                sms_failed = True
                for recipient in sms_recipients:
                    normalized_phone = str(recipient).strip()
                    if not normalized_phone:
                        continue
                    delivery = run.deliveries.filter(channel=NotificationChannel.SMS, recipient_address=normalized_phone).first()
                    if delivery:
                        delivery.status = ReportScheduleDeliveryStatus.FAILED
                        delivery.failed_at = timezone.now()
                        delivery.error_message = str(exc)
                        delivery.save(update_fields=["status", "failed_at", "error_message", "updated_at"])
                schedule.last_delivery_status = "FAILED"
                schedule.last_error_message = str(exc)
                schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])
                run.status = ReportScheduleRunStatus.FAILED
                run.error_message = str(exc)
                run.save(update_fields=["generated_job", "started_at", "completed_at", "status", "error_message", "updated_at"])
                _safe_write_audit(
                    "REPORT_GENERATION_FAILED",
                    "ReportSchedule",
                    schedule.pk,
                    organization=schedule.organization,
                    actor=requested_by,
                    message=str(exc),
                )
                raise

        run.status = ReportScheduleRunStatus.FAILED if (email_failed or sms_failed) else ReportScheduleRunStatus.COMPLETED
        run.error_message = "" if run.status == ReportScheduleRunStatus.COMPLETED else "One or more deliveries failed."
        run.save(update_fields=["generated_job", "started_at", "completed_at", "status", "error_message", "updated_at"])

        schedule.last_sent_at = timezone.now()
        schedule.last_delivery_status = "FAILED" if email_failed else "SENT"
        schedule.last_error_message = "" if not email_failed else "One or more deliveries failed."
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
    except Exception:
        raise
