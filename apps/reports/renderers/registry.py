from __future__ import annotations

from .csv import CSVReportRenderer

REPORT_RENDERER_REGISTRY = {
    "CSV": CSVReportRenderer,
}


def get_report_renderer(format_code: str):
    renderer_cls = REPORT_RENDERER_REGISTRY.get(str(format_code).upper())
    if not renderer_cls:
        return None
    return renderer_cls()
