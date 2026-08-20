from __future__ import annotations

import csv
import hashlib
import os

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


def _template_config(context: GeneratorContext) -> dict:
    template_snapshot = context.template_snapshot if isinstance(context.template_snapshot, dict) else {}
    configuration = template_snapshot.get("configuration")
    return configuration if isinstance(configuration, dict) else {}


def _comment_block_line(text: str = "") -> str:
    return text.rstrip()


def _comment_kv(label: str, value) -> str:
    return f"{label:<18} : {value}"


def _header_lines(context: GeneratorContext) -> list[str]:
    config = _template_config(context)
    header_config = config.get("report_header") if isinstance(config.get("report_header"), dict) else {}
    if str(header_config.get("enabled", True)).lower() in {"false", "0", "no", "off"}:
        return []

    lines: list[str] = []
    lines.append("==========================================================================")
    lines.append("Dhaka Bank DCIM | Report Header")
    lines.append("--------------------------------------------------------------------------")
    lines.append("==========================================================================")
    return lines


def _footer_lines(context: GeneratorContext) -> list[str]:
    config = _template_config(context)
    footer_config = config.get("report_footer") if isinstance(config.get("report_footer"), dict) else {}
    if str(footer_config.get("enabled", True)).lower() in {"false", "0", "no", "off"}:
        return []

    lines: list[str] = []
    lines.append("==========================================================================")
    lines.append("Report Footer")
    custom_text = str(footer_config.get("custom_text") or "").strip()
    if custom_text:
        lines.append(_comment_kv("Footer Text", custom_text))
    if footer_config.get("show_confidentiality_note", True):
        lines.append(_comment_kv("Confidentiality Note", "Confidential - Dhaka Bank DCIM report"))
    if footer_config.get("show_generated_at", False):
        lines.append(_comment_kv("Generated At", context.generated_at.strftime('%d %b %Y, %H:%M')))
    if footer_config.get("show_timezone", False):
        tz_name = getattr(context.timezone, "key", None) or getattr(context.timezone, "zone", None) or str(context.timezone)
        if tz_name:
            lines.append(_comment_kv("Timezone", tz_name))
    lines.append("==========================================================================")
    return lines


def render_csv(dataset: ReportDataset, context: GeneratorContext, output_path: str, filename: str) -> RenderedArtifact:
    table = dataset.primary_table
    if table is None:
        raise ValueError("The dataset does not contain any table to render.")

    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        header_lines = _header_lines(context)
        footer_lines = _footer_lines(context)
        for line in header_lines:
            handle.write(f"{line}\n")
        if header_lines:
            handle.write("\n")
        writer.writerow(list(table.columns))
        for row in table.rows:
            writer.writerow([format_cell_value(row.get(column)) for column in table.columns])
        if footer_lines:
            handle.write("\n")
            for line in footer_lines:
                handle.write(f"{line}\n")
    return _artifact_metadata(output_path, filename)
