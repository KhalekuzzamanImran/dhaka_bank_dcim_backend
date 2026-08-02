from __future__ import annotations

from .base import BaseReportRenderer, RenderedArtifact


class PDFReportRenderer(BaseReportRenderer):
    format_code = "PDF"

    def render(self, *, job, report_data, output_config) -> RenderedArtifact:
        raise NotImplementedError("PDF renderer is not implemented in Phase 2.")
