from __future__ import annotations

from .base import BaseReportHandler, ReportHandlerContext, ReportData


class AlertSummaryHandler(BaseReportHandler):
    code = "alert_summary"

    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        from apps.reports.services.generator import _alert_summary_rows

        headers, rows = _alert_summary_rows(context.job)
        return ReportData(headers=headers, rows=rows, report_type=self.code)


class AlertExportHandler(BaseReportHandler):
    code = "alert_export"

    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        from apps.reports.services.generator import _alert_export_rows

        headers, rows = _alert_export_rows(context.job)
        return ReportData(headers=headers, rows=rows, report_type=self.code)
