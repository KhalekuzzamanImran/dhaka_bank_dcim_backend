from __future__ import annotations

from django.utils import timezone

from apps.notifications.services.sms import send_sms_message

from .base import DeliveryResult


def send_report_sms_delivery(*, delivery, job, artifact=None):
    message = (
        f"Report ready: {getattr(job.template, 'name', 'Report')}. "
        f"Generated at {timezone.localtime(job.completed_at or timezone.now()).strftime('%d %b %Y %I:%M %p')}."
    )
    response = send_sms_message(delivery.destination_snapshot, message)
    return DeliveryResult(accepted=True, delivered=True, provider_response=response or {})
