from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import asdict

from .base import GeneratorContext, ReportDataset, RenderedArtifact
from .datasets import format_cell_value


def _artifact_metadata(path: str, filename: str) -> RenderedArtifact:
    size_bytes = os.path.getsize(path)
    sha256 = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            sha256.update(chunk)
    return RenderedArtifact(
        format="CSV",
        path=path,
        filename=filename,
        content_type="text/csv",
        size_bytes=size_bytes,
        checksum_sha256=sha256.hexdigest(),
    )


def render_csv(dataset: ReportDataset, context: GeneratorContext, output_path: str, filename: str) -> RenderedArtifact:
    table = dataset.primary_table
    if table is None:
        raise ValueError("The dataset does not contain any table to render.")

    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(table.columns))
        for row in table.rows:
            writer.writerow([format_cell_value(row.get(column)) for column in table.columns])
    return _artifact_metadata(output_path, filename)
