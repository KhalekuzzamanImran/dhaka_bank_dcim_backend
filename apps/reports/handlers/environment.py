from __future__ import annotations

from .base import BaseReportHandler, ReportHandlerContext, ReportData


class RoomEnvironmentHandler(BaseReportHandler):
    code = "room_environment"

    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        from apps.reports.services.generator import _room_environment_rows

        headers, rows = _room_environment_rows(context.job)
        return ReportData(headers=headers, rows=rows, report_type=self.code)
