from __future__ import annotations

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import parse_date_range
from .registry import register_generator


@register_generator("notification_delivery")
class NotificationDeliveryGenerator(BaseReportGenerator):
    definition_code = "NOTIFICATION_DELIVERY"
    generator_key = "notification_delivery"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        from apps.notifications.models import NotificationDelivery, NotificationStatus

        parameters = context.parameters or {}
        qs = NotificationDelivery.objects.select_related("notification", "notification__recipient", "notification__organization").filter(
            notification__organization_id=context.organization.id
        )
        if context.data_center:
            qs = qs.filter(notification__metadata__data_center_id=str(context.data_center.id))
        start_dt, end_dt = parse_date_range(parameters)
        if start_dt:
            qs = qs.filter(notification__created_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(notification__created_at__lte=end_dt)
        if parameters.get("recipient_id"):
            qs = qs.filter(notification__recipient_id=parameters["recipient_id"])
        if parameters.get("channel"):
            qs = qs.filter(channel=parameters["channel"])
        if parameters.get("status"):
            qs = qs.filter(status=parameters["status"])

        rows = [
            {"section": "summary", "label": "total", "value": qs.count()},
            {"section": "summary", "label": "pending", "value": qs.filter(status=NotificationStatus.PENDING).count()},
            # NotificationStatus uses PENDING for deliveries waiting to be
            # processed; it has no separate QUEUED enum value.
            {"section": "summary", "label": "queued", "value": qs.filter(status=NotificationStatus.PENDING).count()},
            {"section": "summary", "label": "delivering", "value": qs.filter(status=NotificationStatus.DELIVERING).count()},
            {"section": "summary", "label": "sent", "value": qs.filter(status=NotificationStatus.SENT).count()},
            {"section": "summary", "label": "failed", "value": qs.filter(status=NotificationStatus.FAILED).count()},
        ]
        rows.extend(
            {
                "section": "delivery",
                "label": str(delivery.pk),
                "value": delivery.status,
                "notification_id": str(delivery.notification_id),
                "subject": delivery.notification.subject,
                "recipient": getattr(delivery.notification.recipient, "username", None) or getattr(delivery.notification.recipient, "email", None),
                "recipient_address": delivery.recipient_address,
                "channel": delivery.channel,
                "status": delivery.status,
                "queued_at": delivery.queued_at,
                "sent_at": delivery.sent_at,
                "failed_at": delivery.failed_at,
                "error_message": delivery.error_message,
            }
            for delivery in qs.select_related("notification__recipient").order_by("-created_at", "-id").iterator(chunk_size=1000)
        )
        return ReportDataset(
            title="Notification Delivery",
            subtitle="Notification delivery history",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Deliveries",
                    columns=[
                        "section",
                        "label",
                        "value",
                        "notification_id",
                        "subject",
                        "recipient",
                        "recipient_address",
                        "channel",
                        "status",
                        "queued_at",
                        "sent_at",
                        "failed_at",
                        "error_message",
                    ],
                    rows=rows,
                    primary=True,
                )
            ],
        )
