from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.audit import write_audit
from apps.notifications.models import Notification, NotificationChannel, NotificationDelivery, NotificationStatus
from apps.notifications.services.delivery import deliver_notification_delivery
from apps.notifications.services.sms import _normalize_bangladesh_mobile
from .observability import log_report_event, log_report_metric

from ..enums import ReportDeliveryStatus
from ..models import (
    ReportArtifact,
    ReportDelivery,
    ReportJob,
    ReportJobStatus,
)
from .artifacts import MIME_TYPES


logger = logging.getLogger(__name__)

_email_validator = EmailValidator()


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write report delivery audit event.", exc_info=True)
        return None


@dataclass(frozen=True)
class DeliveryRecipient:
    channel: str
    recipient: str
    schedule_recipient: object | None = None
    source: str = "snapshot"


@dataclass(frozen=True)
class ReportDeliveryBatchResult:
    deliveries: list[ReportDelivery] = field(default_factory=list)
    created_count: int = 0
    queued_count: int = 0
    skipped_count: int = 0

    def as_dict(self):
        return {
            "deliveries": [str(delivery.pk) for delivery in self.deliveries],
            "created_count": self.created_count,
            "queued_count": self.queued_count,
            "skipped_count": self.skipped_count,
        }


def _normalize_email(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        _email_validator(candidate)
    except ValidationError:
        return None
    return candidate.lower()


def _normalize_sms(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        return _normalize_bangladesh_mobile(candidate)
    except Exception:
        return None


def _masked_recipient(recipient: str) -> str:
    if "@" in recipient:
        user, _, domain = recipient.partition("@")
        return f"{user[:2]}***@{domain}"
    if len(recipient) <= 4:
        return "***"
    return f"{recipient[:3]}***{recipient[-2:]}"


def _artifact_download_url(artifact: ReportArtifact) -> str:
    return f"/api/v1/reports/artifacts/{artifact.pk}/download/"


def _collect_snapshot_recipients(job: ReportJob) -> list[DeliveryRecipient]:
    snapshot = job.recipient_snapshot if isinstance(job.recipient_snapshot, dict) else {}
    recipients: list[DeliveryRecipient] = []

    for value in snapshot.get("email_recipients") or snapshot.get("recipients") or []:
        if isinstance(value, dict):
            normalized = _normalize_email(
                value.get("email_address")
                or value.get("email")
                or value.get("recipient")
                or value.get("address")
                or value.get("value")
            )
            if normalized:
                recipients.append(DeliveryRecipient(channel=NotificationChannel.EMAIL, recipient=normalized, source="snapshot"))
            continue
        normalized = _normalize_email(value)
        if normalized:
            recipients.append(DeliveryRecipient(channel=NotificationChannel.EMAIL, recipient=normalized, source="snapshot"))

    for value in snapshot.get("sms_recipients") or []:
        if isinstance(value, dict):
            normalized = _normalize_sms(
                value.get("phone_number")
                or value.get("phone")
                or value.get("recipient")
                or value.get("address")
                or value.get("value")
            )
            if normalized:
                recipients.append(DeliveryRecipient(channel=NotificationChannel.SMS, recipient=normalized, source="snapshot"))
            continue
        normalized = _normalize_sms(value)
        if normalized:
            recipients.append(DeliveryRecipient(channel=NotificationChannel.SMS, recipient=normalized, source="snapshot"))

    unique: list[DeliveryRecipient] = []
    seen: set[tuple[str, str]] = set()
    for item in recipients:
        key = (item.channel, item.recipient)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _collect_structured_recipients(job: ReportJob) -> list[DeliveryRecipient]:
    recipients: list[DeliveryRecipient] = []
    schedule = job.schedule
    if not schedule:
        return recipients

    for row in schedule.structured_recipients.filter(is_active=True).order_by("created_at", "pk"):
        if row.channel == NotificationChannel.EMAIL:
            normalized = _normalize_email(row.email_address)
        else:
            normalized = _normalize_sms(row.phone_number)
        if not normalized:
            continue
        recipients.append(DeliveryRecipient(channel=row.channel, recipient=normalized, schedule_recipient=row, source="structured"))

    unique: list[DeliveryRecipient] = []
    seen: set[tuple[str, str]] = set()
    for item in recipients:
        key = (item.channel, item.recipient)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def resolve_report_recipients(job: ReportJob, recipients=None) -> list[DeliveryRecipient]:
    if recipients is not None:
        normalized: list[DeliveryRecipient] = []
        for value in recipients:
            if isinstance(value, DeliveryRecipient):
                normalized.append(value)
                continue
            if isinstance(value, dict):
                channel = str(value.get("channel") or "").strip().upper()
                address = value.get("recipient") or value.get("recipient_address") or value.get("email") or value.get("phone")
                if channel == NotificationChannel.EMAIL:
                    normalized_address = _normalize_email(address)
                elif channel == NotificationChannel.SMS:
                    normalized_address = _normalize_sms(address)
                else:
                    normalized_address = None
                if normalized_address:
                    normalized.append(
                        DeliveryRecipient(
                            channel=channel,
                            recipient=normalized_address,
                            source=str(value.get("source") or "manual"),
                        )
                    )
        return normalized

    snapshot_recipients = _collect_snapshot_recipients(job)
    if snapshot_recipients:
        return snapshot_recipients

    return _collect_structured_recipients(job)


def _attachment_payload(artifact: ReportArtifact) -> dict:
    with artifact.file.open("rb") as handle:
        return {
            "filename": Path(artifact.original_filename or artifact.file.name).name,
            "content": handle.read(),
            "mimetype": artifact.content_type or MIME_TYPES.get(artifact.format, "application/octet-stream"),
        }


def _build_email_subject(job: ReportJob) -> str:
    prefix = str(getattr(settings, "REPORT_EMAIL_SUBJECT_PREFIX", "DCIM Report") or "DCIM Report").strip()
    report_name = getattr(job.template, "name", None) or getattr(job.definition, "name", None) or "Report"
    period = []
    parameters = job.parameters_snapshot if isinstance(job.parameters_snapshot, dict) else {}
    for key in ("date_from", "start_date", "window_start"):
        if parameters.get(key):
            period.append(str(parameters.get(key)))
            break
    for key in ("date_to", "end_date", "window_end"):
        if parameters.get(key):
            period.append(str(parameters.get(key)))
            break
    if len(period) == 2:
        return f"{prefix}: {report_name} ({period[0]} - {period[1]})"
    return f"{prefix}: {report_name}"


def _build_email_body(job: ReportJob, delivery: ReportDelivery, artifacts: list[ReportArtifact]) -> str:
    lines = [
        f"Report: {getattr(job.template, 'name', None) or getattr(job.definition, 'name', None) or 'Report'}",
        f"Organization: {getattr(job.organization, 'name', None) or job.organization_id}",
        f"Data center: {getattr(job.data_center, 'name', None) or job.data_center_id or '--'}",
        f"Generated: {timezone.localtime(job.completed_at or timezone.now()).strftime('%d %b %Y, %H:%M')}",
        f"Status: {job.status}",
        f"Delivery: {delivery.channel}",
    ]
    if artifacts:
        lines.append("")
        lines.append("Artifacts:")
        for artifact in artifacts:
            if artifact.size_bytes <= getattr(settings, "REPORT_EMAIL_ATTACHMENT_MAX_BYTES", 5 * 1024 * 1024):
                lines.append(f"- {artifact.original_filename} (attached)")
            else:
                lines.append(f"- {artifact.original_filename}: {_artifact_download_url(artifact)}")
    return "\n".join(lines)


def _build_sms_body(job: ReportJob, delivery: ReportDelivery, artifacts: list[ReportArtifact]) -> str:
    report_name = getattr(job.template, "name", None) or getattr(job.definition, "name", None) or "Report"
    status = job.status
    parts = [
        f"DCIM report ready: {report_name}.",
        f"Status: {status}.",
        f"Channel: {delivery.channel}.",
    ]
    if artifacts:
        parts.append(f"Download: {_artifact_download_url(artifacts[0])}")
    message = " ".join(parts)
    max_length = int(getattr(settings, "REPORT_SMS_MAX_LENGTH", 480))
    return message if len(message) <= max_length else f"{message[: max_length - 3].rstrip()}..."


def _create_notification_delivery(report_delivery: ReportDelivery) -> NotificationDelivery:
    notification, _ = Notification.objects.get_or_create(
        organization=report_delivery.job.organization,
        dedupe_key=f"report_delivery:{report_delivery.job_id}:{report_delivery.channel}:{report_delivery.recipient}",
        defaults={
            "recipient": report_delivery.job.requested_by if report_delivery.job.requested_by_id else None,
            "subject": _build_email_subject(report_delivery.job),
            "message": report_delivery.job.template.name if report_delivery.job.template_id else (report_delivery.job.definition.name if report_delivery.job.definition_id else "Report"),
            "metadata": {
                "report_job_id": str(report_delivery.job_id),
                "report_delivery_id": str(report_delivery.pk),
                "channel": report_delivery.channel,
                "recipient": report_delivery.recipient,
            },
        },
    )
    notification.subject = _build_email_subject(report_delivery.job)
    notification.message = _build_email_body(report_delivery.job, report_delivery, list(report_delivery.job.artifacts.order_by("created_at", "pk")))
    metadata = notification.metadata if isinstance(notification.metadata, dict) else {}
    metadata = deepcopy(metadata)
    metadata.update(
        {
            "report_job_id": str(report_delivery.job_id),
            "report_delivery_id": str(report_delivery.pk),
            "channel": report_delivery.channel,
            "recipient": report_delivery.recipient,
            "artifact_ids": [str(artifact.pk) for artifact in report_delivery.job.artifacts.order_by("created_at", "pk")],
        }
    )
    notification.metadata = metadata
    notification.save(update_fields=["subject", "message", "metadata", "updated_at"])

    notification_delivery, created = NotificationDelivery.objects.get_or_create(
        notification=notification,
        channel=report_delivery.channel,
        defaults={
            "report_delivery": report_delivery,
            "recipient_address": report_delivery.recipient,
            "status": NotificationStatus.PENDING,
            "attempt_count": 0,
            "max_attempts": int(getattr(settings, "REPORT_DELIVERY_MAX_RETRIES", 3)),
            "queued_at": timezone.now(),
            "metadata": {
                "report_job_id": str(report_delivery.job_id),
                "report_delivery_id": str(report_delivery.pk),
                "recipient": report_delivery.recipient,
                "channel": report_delivery.channel,
            },
        },
    )
    if not created:
        if notification_delivery.report_delivery_id != report_delivery.id:
            notification_delivery.report_delivery = report_delivery
        if not notification_delivery.recipient_address:
            notification_delivery.recipient_address = report_delivery.recipient
        notification_delivery.metadata = {
            **(notification_delivery.metadata if isinstance(notification_delivery.metadata, dict) else {}),
            "report_job_id": str(report_delivery.job_id),
            "report_delivery_id": str(report_delivery.pk),
            "recipient": report_delivery.recipient,
            "channel": report_delivery.channel,
        }
        notification_delivery.save(update_fields=["report_delivery", "recipient_address", "metadata", "updated_at"])
    return notification_delivery


def report_delivery_summary(job: ReportJob) -> str:
    deliveries = list(job.deliveries.all())
    if not deliveries:
        return "NONE"
    statuses = [delivery.status for delivery in deliveries]
    if any(status == ReportDeliveryStatus.DELIVERING for status in statuses):
        return "IN_PROGRESS"
    if any(status in {ReportDeliveryStatus.PENDING, ReportDeliveryStatus.QUEUED} for status in statuses):
        return "PENDING"
    if all(status == ReportDeliveryStatus.SENT for status in statuses):
        return "SENT"
    if any(status == ReportDeliveryStatus.SENT for status in statuses) and any(status == ReportDeliveryStatus.FAILED for status in statuses):
        return "PARTIAL"
    if all(status == ReportDeliveryStatus.FAILED for status in statuses):
        return "FAILED"
    if any(status == ReportDeliveryStatus.FAILED for status in statuses):
        return "PARTIAL"
    return "NONE"


def _sync_job_schedule_summary(job: ReportJob):
    if not job.schedule_id:
        return None
    schedule = job.schedule
    if not schedule:
        return None
    summary = report_delivery_summary(job)
    schedule.last_delivery_status = summary
    if summary in {"SENT", "PARTIAL", "FAILED"}:
        schedule.last_sent_at = timezone.now()
        schedule.last_error_message = "" if summary in {"SENT"} else schedule.last_error_message
    schedule.save(update_fields=["last_delivery_status", "last_sent_at", "last_error_message", "updated_at"])
    return schedule


def sync_report_delivery_from_notification_delivery(notification_delivery: NotificationDelivery, *, provider_response=None, error_message: str | None = None):
    report_delivery = getattr(notification_delivery, "report_delivery", None)
    if not report_delivery:
        return None

    with transaction.atomic():
        locked = (
            ReportDelivery.objects.select_for_update()
            .select_related("job", "job__organization")
            .filter(pk=report_delivery.pk)
            .first()
        )
        if not locked:
            return None
        locked.status = _map_notification_status(notification_delivery.status)
        locked.queued_at = locked.queued_at or notification_delivery.queued_at or timezone.now()
        locked.started_at = locked.started_at or notification_delivery.delivering_at or notification_delivery.queued_at or timezone.now()
        locked.provider_message_id = notification_delivery.provider_message_id or locked.provider_message_id
        locked.provider_response = notification_delivery.provider_response if isinstance(notification_delivery.provider_response, dict) else (provider_response if isinstance(provider_response, dict) else locked.provider_response)
        locked.error_message = notification_delivery.error_message or error_message or locked.error_message
        if locked.status == ReportDeliveryStatus.SENT:
            locked.sent_at = notification_delivery.sent_at or timezone.now()
            locked.failed_at = None
            locked.error_code = ""
            locked.error_message = ""
        elif locked.status == ReportDeliveryStatus.FAILED:
            locked.failed_at = notification_delivery.failed_at or timezone.now()
            locked.error_code = notification_delivery.error_message and "NotificationDeliveryError" or locked.error_code
        locked.save(
            update_fields=[
                "status",
                "queued_at",
                "started_at",
                "sent_at",
                "failed_at",
                "provider_message_id",
                "provider_response",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
        _sync_job_schedule_summary(locked.job)
        return locked


def create_report_deliveries_for_job(*, job, recipients=None):
    if not job:
        raise ValidationError({"job": "Job is required."})
    if str(job.status) != ReportJobStatus.COMPLETED:
        raise ValidationError({"job": "Report deliveries can only be created for completed jobs."})

    artifacts = list(job.artifacts.order_by("created_at", "pk"))
    if not artifacts:
        raise ValidationError({"job": "At least one report artifact is required before delivery."})

    resolved_recipients = resolve_report_recipients(job, recipients=recipients)
    if not resolved_recipients:
        return ReportDeliveryBatchResult().as_dict()

    created_deliveries: list[ReportDelivery] = []
    created_count = 0
    queued_count = 0

    with transaction.atomic():
        locked_job = ReportJob.objects.select_for_update().select_related("organization").get(pk=job.pk)
        artifacts = list(locked_job.artifacts.order_by("created_at", "pk"))
        if not artifacts:
            raise ValidationError({"job": "At least one report artifact is required before delivery."})
        for recipient in resolved_recipients:
            defaults = {
                "status": ReportDeliveryStatus.PENDING,
                "queued_at": timezone.now(),
                "schedule_recipient": recipient.schedule_recipient,
            }
            delivery, created = ReportDelivery.objects.get_or_create(
                job=locked_job,
                channel=recipient.channel,
                recipient=recipient.recipient,
                defaults=defaults,
            )
            if created:
                created_count += 1
                log_report_event(
                    logger,
                    "Report delivery created",
                    job=locked_job,
                    delivery_count=1,
                    retry_count=0,
                )
                _safe_write_audit(
                    "REPORT_DELIVERY_CREATED",
                    "ReportDelivery",
                    delivery.pk,
                    organization=locked_job.organization,
                    actor=locked_job.requested_by,
                    message="Report delivery created",
                    new_value={
                        "job_id": str(locked_job.pk),
                        "delivery_id": str(delivery.pk),
                        "channel": delivery.channel,
                        "recipient": _masked_recipient(delivery.recipient),
                        "organization_id": str(locked_job.organization_id) if locked_job.organization_id else None,
                        "data_center_id": str(locked_job.data_center_id) if locked_job.data_center_id else None,
                    },
                )
            elif delivery.status in {ReportDeliveryStatus.SENT, ReportDeliveryStatus.DELIVERING}:
                created_deliveries.append(delivery)
                continue

            notification_delivery = _create_notification_delivery(delivery)
            if delivery.schedule_recipient_id is None and recipient.schedule_recipient is not None:
                delivery.schedule_recipient = recipient.schedule_recipient
            if delivery.status != ReportDeliveryStatus.PENDING:
                delivery.status = ReportDeliveryStatus.PENDING
            if not delivery.queued_at:
                delivery.queued_at = timezone.now()
            delivery.save(update_fields=["schedule_recipient", "status", "queued_at", "updated_at"])
            created_deliveries.append(delivery)

    for delivery in created_deliveries:
        queued = queue_report_delivery(delivery)
        if queued:
            queued_count += 1

    return ReportDeliveryBatchResult(
        deliveries=created_deliveries,
        created_count=created_count,
        queued_count=queued_count,
        skipped_count=max(0, len(resolved_recipients) - len(created_deliveries)),
    ).as_dict()


def queue_report_delivery(*, delivery: ReportDelivery):
    if delivery is None:
        raise ValidationError({"delivery": "Delivery is required."})

    with transaction.atomic():
        locked = (
            ReportDelivery.objects.select_for_update()
            .select_related("job", "job__organization")
            .filter(pk=delivery.pk)
            .first()
        )
        if not locked:
            return None
        if locked.status in {ReportDeliveryStatus.SENT, ReportDeliveryStatus.DELIVERING, ReportDeliveryStatus.CANCELLED}:
            return locked
        locked.status = ReportDeliveryStatus.QUEUED
        locked.queued_at = locked.queued_at or timezone.now()
        locked.save(update_fields=["status", "queued_at", "updated_at"])
        log_report_event(logger, "Report delivery queued", job=locked.job, delivery_count=1, trigger_source=locked.job.trigger_source)
        log_report_metric(logger, "report_deliveries_queued", job=locked.job, delivery_count=1)
        _safe_write_audit(
            "REPORT_DELIVERY_QUEUED",
            "ReportDelivery",
            locked.pk,
            organization=locked.job.organization,
            actor=locked.job.requested_by,
            message="Report delivery queued",
            new_value={
                "job_id": str(locked.job_id),
                "delivery_id": str(locked.pk),
                "channel": locked.channel,
                "recipient": _masked_recipient(locked.recipient),
            },
        )

        from apps.reports.tasks import execute_report_delivery_task

        def _dispatch():
            execute_report_delivery_task.delay(str(locked.pk))

        transaction.on_commit(_dispatch)
        return locked


def _claim_report_delivery(delivery_id):
    with transaction.atomic():
        delivery = (
            ReportDelivery.objects.select_for_update()
            .select_related("job", "job__organization")
            .filter(pk=delivery_id)
            .first()
        )
        if not delivery:
            return None
        if delivery.status in {ReportDeliveryStatus.SENT, ReportDeliveryStatus.CANCELLED}:
            return None
        if str(delivery.job.status) != ReportJobStatus.COMPLETED:
            raise ValidationError({"job": "Report delivery can only run for completed jobs."})
        if not delivery.job.artifacts.exists():
            raise ValidationError({"job": "Report delivery requires at least one persisted artifact."})
        delivery.status = ReportDeliveryStatus.DELIVERING
        delivery.started_at = delivery.started_at or timezone.now()
        delivery.save(update_fields=["status", "started_at", "updated_at"])
        log_report_event(logger, "Report delivery started", job=delivery.job, delivery_count=1, trigger_source=delivery.job.trigger_source)
        _safe_write_audit(
            "REPORT_DELIVERY_STARTED",
            "ReportDelivery",
            delivery.pk,
            organization=delivery.job.organization,
            actor=delivery.job.requested_by,
            message="Report delivery started",
            new_value={
                "job_id": str(delivery.job_id),
                "delivery_id": str(delivery.pk),
                "channel": delivery.channel,
                "recipient": _masked_recipient(delivery.recipient),
            },
        )
        return delivery


def _build_provider_payload(delivery: ReportDelivery):
    artifacts = list(delivery.job.artifacts.order_by("created_at", "pk"))
    if delivery.channel == NotificationChannel.EMAIL:
        attachments = []
        body_lines = []
        for artifact in artifacts:
            if artifact.size_bytes <= int(getattr(settings, "REPORT_EMAIL_ATTACHMENT_MAX_BYTES", 5 * 1024 * 1024)):
                attachments.append(_attachment_payload(artifact))
            else:
                body_lines.append(f"Download {artifact.original_filename}: {_artifact_download_url(artifact)}")
        body = _build_email_body(delivery.job, delivery, artifacts)
        if body_lines:
            body = "\n".join([body, "", *body_lines])
        return {
            "email_subject": _build_email_subject(delivery.job),
            "email_body": body,
            "email_attachments": attachments,
        }
    if delivery.channel == NotificationChannel.SMS:
        return {"sms_message": _build_sms_body(delivery.job, delivery, artifacts)}
    return {}


def _finalize_delivery_state(report_delivery: ReportDelivery, notification_delivery: NotificationDelivery, provider_response=None, error: Exception | None = None):
    now = timezone.now()
    with transaction.atomic():
        locked = ReportDelivery.objects.select_for_update().select_related("job", "job__organization").get(pk=report_delivery.pk)
        if error is None:
            locked.status = ReportDeliveryStatus.SENT
            locked.sent_at = locked.sent_at or now
            locked.failed_at = None
            locked.error_code = ""
            locked.error_message = ""
            locked.provider_response = provider_response if isinstance(provider_response, dict) else (notification_delivery.provider_response or {})
            locked.provider_message_id = notification_delivery.provider_message_id or locked.provider_message_id
            locked.save(update_fields=["status", "sent_at", "failed_at", "error_code", "error_message", "provider_response", "provider_message_id", "updated_at"])
            log_report_event(logger, "Report delivery sent", job=locked.job, delivery_count=1)
            log_report_metric(logger, "report_deliveries_sent", job=locked.job, delivery_count=1)
            _safe_write_audit(
                "REPORT_DELIVERY_SENT",
                "ReportDelivery",
                locked.pk,
                organization=locked.job.organization,
                actor=locked.job.requested_by,
                message="Report delivery sent",
                new_value={
                    "job_id": str(locked.job_id),
                    "delivery_id": str(locked.pk),
                    "channel": locked.channel,
                    "recipient": _masked_recipient(locked.recipient),
                    "provider_message_id": locked.provider_message_id or None,
                },
            )
        else:
            retryable = not isinstance(error, (ValidationError, ValueError))
            locked.error_code = error.__class__.__name__
            locked.error_message = str(error)
            locked.provider_response = notification_delivery.provider_response if isinstance(notification_delivery.provider_response, dict) else locked.provider_response
            if retryable and locked.retry_count < int(getattr(settings, "REPORT_DELIVERY_MAX_RETRIES", 3)):
                locked.retry_count += 1
                locked.status = ReportDeliveryStatus.QUEUED
                locked.queued_at = now
                locked.started_at = locked.started_at or now
                locked.save(update_fields=["status", "retry_count", "queued_at", "started_at", "error_code", "error_message", "provider_response", "updated_at"])
                log_report_event(logger, "Report delivery retry scheduled", job=locked.job, retry_count=locked.retry_count, delivery_count=1)
                log_report_metric(logger, "report_deliveries_retry", job=locked.job, retry_count=locked.retry_count, delivery_count=1)
                _safe_write_audit(
                    "REPORT_DELIVERY_RETRY_REQUESTED",
                    "ReportDelivery",
                    locked.pk,
                    organization=locked.job.organization,
                    actor=locked.job.requested_by,
                    message="Report delivery retry requested",
                    new_value={
                        "job_id": str(locked.job_id),
                        "delivery_id": str(locked.pk),
                        "channel": locked.channel,
                        "recipient": _masked_recipient(locked.recipient),
                        "retry_count": locked.retry_count,
                        "error_code": locked.error_code,
                    },
                )
            else:
                locked.status = ReportDeliveryStatus.FAILED
                locked.failed_at = locked.failed_at or now
                locked.save(update_fields=["status", "failed_at", "error_code", "error_message", "provider_response", "updated_at"])
                log_report_event(logger, "Report delivery failed", job=locked.job, retry_count=locked.retry_count, delivery_count=1)
                log_report_metric(logger, "report_deliveries_failed", job=locked.job, retry_count=locked.retry_count, delivery_count=1)
                _safe_write_audit(
                    "REPORT_DELIVERY_FAILED",
                    "ReportDelivery",
                    locked.pk,
                    organization=locked.job.organization,
                    actor=locked.job.requested_by,
                    message="Report delivery failed",
                    new_value={
                        "job_id": str(locked.job_id),
                        "delivery_id": str(locked.pk),
                        "channel": locked.channel,
                        "recipient": _masked_recipient(locked.recipient),
                        "retry_count": locked.retry_count,
                        "error_code": locked.error_code,
                    },
                )
        _sync_job_schedule_summary(locked.job)
        return locked


def execute_report_delivery(*, delivery_id):
    delivery = _claim_report_delivery(delivery_id)
    if not delivery:
        return None

    notification_delivery = delivery.notification_deliveries.order_by("created_at", "pk").first()
    if not notification_delivery:
        notification_delivery = _create_notification_delivery(delivery)

    notification_delivery.status = NotificationStatus.DELIVERING
    notification_delivery.delivering_at = notification_delivery.delivering_at or timezone.now()
    notification_delivery.attempt_count = (notification_delivery.attempt_count or 0) + 1
    notification_delivery.save(update_fields=["status", "delivering_at", "attempt_count", "updated_at"])

    provider_kwargs = _build_provider_payload(delivery)
    try:
        deliver_notification_delivery(notification_delivery, **provider_kwargs)
        notification_delivery.refresh_from_db()
        final_delivery = _finalize_delivery_state(delivery, notification_delivery, provider_response=notification_delivery.provider_response)
        log_report_metric(logger, "report_deliveries_sent", job=delivery.job, delivery_count=1)
        return final_delivery
    except Exception as exc:
        notification_delivery = NotificationDelivery.objects.filter(pk=notification_delivery.pk).first() or notification_delivery
        notification_delivery.status = NotificationStatus.FAILED if not isinstance(exc, (ValidationError, ValueError)) else NotificationStatus.FAILED
        notification_delivery.failed_at = timezone.now()
        notification_delivery.error_message = str(exc)
        notification_delivery.save(update_fields=["status", "failed_at", "error_message", "updated_at"])
        final_delivery = _finalize_delivery_state(delivery, notification_delivery, error=exc)
        if final_delivery.status == ReportDeliveryStatus.QUEUED:
            log_report_event(logger, "Retrying report delivery later", job=delivery.job, retry_count=delivery.retry_count)
            raise exc
        raise


def retry_report_delivery(*, delivery, requested_by=None):
    if not delivery:
        raise ValidationError({"delivery": "Delivery is required."})
    with transaction.atomic():
        locked = ReportDelivery.objects.select_for_update().select_related("job", "job__organization").filter(pk=delivery.pk).first()
        if not locked:
            return None
        if locked.status != ReportDeliveryStatus.FAILED:
            raise ValidationError({"delivery": "Only failed report deliveries can be retried."})
        max_retries = int(getattr(settings, "REPORT_DELIVERY_MAX_RETRIES", 3))
        if locked.retry_count >= max_retries:
            raise ValidationError({"delivery": "Report delivery retry limit reached."})
        locked.retry_count += 1
        locked.status = ReportDeliveryStatus.QUEUED
        locked.queued_at = timezone.now()
        locked.error_code = ""
        locked.error_message = ""
        locked.save(update_fields=["retry_count", "status", "queued_at", "error_code", "error_message", "updated_at"])
        notification_delivery = locked.notification_deliveries.order_by("created_at", "pk").first()
        if notification_delivery:
            notification_delivery.status = NotificationStatus.PENDING
            notification_delivery.error_message = ""
            notification_delivery.failed_at = None
            notification_delivery.sent_at = None
            notification_delivery.delivering_at = None
            notification_delivery.queued_at = locked.queued_at
            notification_delivery.attempt_count = max(notification_delivery.attempt_count or 0, locked.retry_count)
            notification_delivery.save(update_fields=["status", "error_message", "failed_at", "sent_at", "delivering_at", "queued_at", "attempt_count", "updated_at"])
        _safe_write_audit(
            "REPORT_DELIVERY_RETRY_REQUESTED",
            "ReportDelivery",
            locked.pk,
            organization=locked.job.organization,
            actor=requested_by or locked.job.requested_by,
            message="Report delivery retry requested",
            new_value={
                "job_id": str(locked.job_id),
                "delivery_id": str(locked.pk),
                "channel": locked.channel,
                "recipient": _masked_recipient(locked.recipient),
                "retry_count": locked.retry_count,
            },
        )

        from apps.reports.tasks import execute_report_delivery_task

        def _dispatch():
            execute_report_delivery_task.delay(str(locked.pk))

        transaction.on_commit(_dispatch)
        return locked
