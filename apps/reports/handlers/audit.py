from __future__ import annotations

from .base import BaseReportHandler, ReportHandlerContext, ReportData


class AuditExportHandler(BaseReportHandler):
    code = "audit_export"

    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        from apps.reports.services.generator import _audit_export_rows

        headers, rows = _audit_export_rows(context.job)
        return ReportData(headers=headers, rows=rows, report_type=self.code)
