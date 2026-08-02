from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from .base import BaseReportRenderer, RenderedArtifact


class CSVReportRenderer(BaseReportRenderer):
    format_code = "CSV"

    def render(self, *, job, report_data, output_config) -> RenderedArtifact:
        suffix = ".csv"
        temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False, suffix=suffix)
        try:
            writer = csv.DictWriter(temp, fieldnames=list(report_data.headers))
            writer.writeheader()
            rows = report_data.rows
            if hasattr(rows, "iterator"):
                rows_iterable = rows.iterator()
            else:
                rows_iterable = rows
            for row in rows_iterable:
                writer.writerow({key: row.get(key) for key in report_data.headers})
            temp.flush()
            size = Path(temp.name).stat().st_size
            return RenderedArtifact(
                file_path=temp.name,
                file_name=Path(temp.name).name,
                content_type="text/csv",
                format="CSV",
                artifact_type="PRIMARY",
                metadata={"size_bytes": size},
            )
        finally:
            temp.close()
