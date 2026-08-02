from __future__ import annotations

from .base import BaseReportHandler, ReportHandlerContext, ReportData


class NotificationDeliveryHandler(BaseReportHandler):
    code = "notification_delivery"

    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        from apps.reports.services.generator import _notification_delivery_rows

        headers, rows = _notification_delivery_rows(context.job)
        return ReportData(headers=headers, rows=rows, report_type=self.code)
