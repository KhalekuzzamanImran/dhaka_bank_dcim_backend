from __future__ import annotations

from .base import BaseReportRenderer, RenderedArtifact


class XLSXReportRenderer(BaseReportRenderer):
    format_code = "XLSX"

    def render(self, *, job, report_data, output_config) -> RenderedArtifact:
        raise NotImplementedError("XLSX renderer is not implemented in Phase 2.")
