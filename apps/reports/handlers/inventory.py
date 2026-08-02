from __future__ import annotations

from .base import BaseReportHandler, ReportHandlerContext, ReportData


class DeviceInventoryHandler(BaseReportHandler):
    code = "device_inventory"

    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        from apps.reports.services.generator import _device_inventory_rows

        headers, rows = _device_inventory_rows(context.job)
        return ReportData(headers=headers, rows=rows, report_type=self.code)
