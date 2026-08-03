from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from copy import deepcopy

from django.db import IntegrityError, transaction

from django.conf import settings
from django.core.mail import EmailMessage
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
    ReportScheduleStatus,
)
from .generator import generate_report_job

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
    scheduled_for,
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
    scheduled_for_dt = parse_datetime(scheduled_for) if scheduled_for else None
    if scheduled_for_dt is None and trigger_source == "SCHEDULED":
        scheduled_for_dt = schedule.next_run_at
    if window_end_dt is None:
        window_end_dt = scheduled_for_dt or timezone.now()
    if window_start_dt is None:
        window_start_dt, _ = schedule.calculate_execution_window(reference_time=scheduled_for_dt or window_end_dt)

    template = schedule.template if schedule.template_id else None
    template_defaults = deepcopy(template.default_parameters) if template and isinstance(template.default_parameters, dict) else {}
    schedule_overrides = deepcopy(schedule.parameter_overrides) if isinstance(schedule.parameter_overrides, dict) else {}
    runtime_parameters = {
        "report_type": schedule.report_type,
        "output_format": schedule.output_format,
        "schedule_id": str(schedule.pk),
        "schedule_name": schedule.name,
        "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
        "requested_format": schedule.output_format,
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
        "report_type": schedule.report_type,
        "output_format": schedule.output_format,
        "primary_format": template.primary_format if template and template.primary_format else schedule.output_format,
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
        "email_recipients": deepcopy(schedule.normalize_recipients()),
        "sms_recipients": deepcopy(schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []),
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

        run = None
        if trigger_source == "SCHEDULED" and scheduled_for_dt is not None:
            run_defaults = {
                "organization": schedule.organization,
                "requested_by": requested_by,
                "window_start": window_start_dt,
                "window_end": window_end_dt,
                "status": ReportScheduleRunStatus.PENDING,
                "queued_at": timezone.now(),
                "snapshot": {
                    "schedule_id": str(schedule.pk),
                    "schedule_name": schedule.name,
                    "report_type": schedule.report_type,
                    "frequency": schedule.frequency,
                    "delivery_time": schedule.delivery_time.strftime("%H:%M:%S"),
                    "output_format": schedule.output_format,
                    "recipients": schedule.normalize_recipients(),
                    "sms_recipients": list(schedule.sms_recipients or []) if isinstance(schedule.sms_recipients, list) else [],
                    "attach_raw_data": schedule.attach_raw_data,
                    "parameters": deepcopy(parameters),
                    "scheduled_for": scheduled_for_dt.isoformat(),
                    "trigger_source": trigger_source,
                },
            }
            try:
                run, created = ReportScheduleRun.objects.get_or_create(
                    schedule=schedule,
                    scheduled_for=scheduled_for_dt,
                    trigger_source="SCHEDULED",
                    defaults=run_defaults,
                )
            except IntegrityError:
                logger.info("Duplicate scheduled run detected schedule=%s scheduled_for=%s", schedule.pk, scheduled_for_dt)
                return schedule
            if not created:
                logger.info("Scheduled run already exists schedule=%s scheduled_for=%s", schedule.pk, scheduled_for_dt)
                return schedule
        else:
            run = _create_schedule_run(
                schedule,
                requested_by=requested_by,
                window_start=window_start_dt,
                window_end=window_end_dt,
                scheduled_for=None,
                trigger_source=trigger_source,
                parameters=parameters,
            )

        job_kwargs = {
            "organization": schedule.organization,
            "data_center": schedule.data_center,
            "schedule": schedule,
            "requested_by": requested_by,
            "status": ReportJobStatus.PENDING,
            "parameters": parameters,
            "parameters_snapshot": parameters_snapshot,
            "template_snapshot": template_snapshot,
            "template_config_snapshot": deepcopy(template.config if template and isinstance(template.config, dict) else {}),
            "output_config_snapshot": output_config_snapshot,
            "scope_snapshot": scope_snapshot,
            "recipient_snapshot": recipient_snapshot,
            "source_event_snapshot": {},
            "trigger_source": trigger_source,
        }
        if template:
            job_kwargs["template"] = template
            job_kwargs["definition"] = template.definition
        job = ReportJob.objects.create(**job_kwargs)

        schedule.last_run_at = scheduled_for_dt or window_end_dt
        schedule.next_run_at = schedule.calculate_next_run_at(reference_time=scheduled_for_dt or window_end_dt)
        schedule.last_delivery_status = "PENDING"
        schedule.last_error_message = ""
        schedule.last_job = job
        schedule.save(update_fields=["last_job", "last_run_at", "next_run_at", "last_delivery_status", "last_error_message", "updated_at"])

    try:
        generated_job = generate_report_job(job.id)
        run.job = generated_job
        run.generated_job = generated_job
        run.started_at = generated_job.started_at or timezone.now()
        run.completed_at = generated_job.completed_at or timezone.now()
        if generated_job.status != ReportJobStatus.COMPLETED or not generated_job.file:
            run.status = ReportScheduleRunStatus.FAILED
            run.error_message = generated_job.error_message or "Scheduled report generation failed."
            run.save(update_fields=["job", "generated_job", "started_at", "completed_at", "status", "error_message", "updated_at"])
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
        run.save(update_fields=["job", "generated_job", "started_at", "completed_at", "status", "error_message", "updated_at"])

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
