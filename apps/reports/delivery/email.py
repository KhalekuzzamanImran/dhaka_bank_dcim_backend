from __future__ import annotations

from django.core.mail import EmailMessage
from django.utils import timezone

from .base import DeliveryResult


def send_report_email_delivery(*, delivery, job, artifact):
    recipient = delivery.destination_snapshot
    message = EmailMessage(
        subject=f"{getattr(job.template, 'name', 'Report')} - Ready",
        body=(
            f"Report '{getattr(job.template, 'name', 'Report')}' is ready.\n"
            f"Generated at: {timezone.localtime(job.completed_at or timezone.now()).isoformat()}\n"
            f"Job ID: {job.pk}"
        ),
        to=[recipient],
    )
    with artifact.file.open("rb") as handle:
        message.attach(artifact.file_name or artifact.file.name.rsplit("/", 1)[-1], handle.read(), artifact.content_type or "application/octet-stream")
    message.send(fail_silently=False)
    return DeliveryResult(accepted=True, delivered=True)
