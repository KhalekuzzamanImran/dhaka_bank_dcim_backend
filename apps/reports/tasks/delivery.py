from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.reports.delivery.email import send_report_email_delivery
from apps.reports.delivery.sms import send_report_sms_delivery
from apps.reports.models import ReportDeliveryStatus, ReportRecipientChannel

logger = logging.getLogger(__name__)


def deliver_report_task_impl(delivery_id):
    from apps.reports.models import ReportDelivery

    with transaction.atomic():
        delivery = ReportDelivery.objects.select_for_update().select_related("job").filter(pk=delivery_id).first()
        if not delivery:
            raise ValueError(f"Report delivery {delivery_id} does not exist.")
        if delivery.status in {ReportDeliveryStatus.DELIVERED, ReportDeliveryStatus.ACCEPTED}:
            return delivery
        delivery.status = ReportDeliveryStatus.SENDING
        delivery.attempted_at = timezone.now()
        delivery.save(update_fields=["status", "attempted_at", "updated_at"])

    try:
        if delivery.channel == ReportRecipientChannel.EMAIL:
            result = send_report_email_delivery(delivery=delivery, job=delivery.job, artifact=delivery.artifact)
        elif delivery.channel == ReportRecipientChannel.SMS:
            result = send_report_sms_delivery(delivery=delivery, job=delivery.job, artifact=delivery.artifact)
        else:
            raise ValueError("Unsupported delivery channel.")
        with transaction.atomic():
            delivery = ReportDelivery.objects.select_for_update().get(pk=delivery.pk)
            delivery.status = ReportDeliveryStatus.DELIVERED if result.delivered else ReportDeliveryStatus.ACCEPTED
            delivery.accepted_at = delivery.accepted_at or timezone.now()
            delivery.delivered_at = timezone.now() if result.delivered else delivery.delivered_at
            delivery.provider_response = result.provider_response or {}
            delivery.provider_message_id = result.provider_message_id
            delivery.save(update_fields=["status", "accepted_at", "delivered_at", "provider_response", "provider_message_id", "updated_at"])
            if result.delivered and delivery.schedule_id:
                delivery.schedule.last_sent_at = delivery.delivered_at or timezone.now()
                delivery.schedule.save(update_fields=["last_sent_at", "updated_at"])
        return delivery
    except Exception as exc:
        logger.exception("Report delivery failed delivery=%s", delivery_id)
        with transaction.atomic():
            delivery = ReportDelivery.objects.select_for_update().get(pk=delivery.pk)
            delivery.status = ReportDeliveryStatus.FAILED
            delivery.failed_at = timezone.now()
            delivery.error_message = str(exc)
            delivery.save(update_fields=["status", "failed_at", "error_message", "updated_at"])
        return delivery
