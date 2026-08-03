from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Iterable

from .base import GeneratorContext, ReportDataset, ReportTable, RenderedArtifact
from .datasets import format_cell_value


def _artifact_metadata(path: str, filename: str) -> RenderedArtifact:
    size_bytes = os.path.getsize(path)
    sha256 = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            sha256.update(chunk)
    return RenderedArtifact(
        format="PDF",
        path=path,
        filename=filename,
        content_type="application/pdf",
        size_bytes=size_bytes,
        checksum_sha256=sha256.hexdigest(),
    )


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _lines_for_table(table: ReportTable) -> list[str]:
    lines = [" | ".join(table.columns)]
    lines.append("-" * max(20, len(lines[0])))
    for row in table.rows:
        lines.append(" | ".join(str(format_cell_value(row.get(column))) for column in table.columns))
    return lines


def _paginate_lines(lines: list[str], *, max_lines_per_page: int = 40) -> list[list[str]]:
    pages: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if len(current) >= max_lines_per_page:
            pages.append(current)
            current = []
    if current or not pages:
        pages.append(current)
    return pages


def _build_page_stream(lines: list[str], page_number: int, total_pages: int) -> bytes:
    y = 760
    commands = [
        "BT",
        "/F1 11 Tf",
        "1 0 0 1 40 780 Tm",
    ]
    for line in lines:
        commands.append(f"({_escape_pdf_text(line[:110])}) Tj")
        y -= 14
        if y <= 60:
            break
        commands.append(f"0 -14 Td")
    commands.append(f"0 -20 Td")
    commands.append(f"({ _escape_pdf_text(f'Page {page_number} of {total_pages}') }) Tj")
    commands.append("ET")
    content = "\n".join(commands).encode("utf-8")
    return content


def _write_pdf(output_path: str, pages: list[list[str]], title: str, footer: str) -> None:
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for page_index, page_lines in enumerate(pages, start=1):
        stream = _build_page_stream(page_lines, page_index, len(pages))
        content_id = add_object(f"<< /Length {len(stream)} >>\nstream\n".encode("utf-8") + stream + b"\nendstream")
        page_id = add_object(
            (
                "<< /Type /Page /Parent 0 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("utf-8")
        )
        page_ids.append(page_id)

    pages_id = add_object(
        ("<< /Type /Pages /Kids [" + " ".join(f"{page_id} 0 R" for page_id in page_ids) + f"] /Count {len(page_ids)} >>").encode("utf-8")
    )
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("utf-8"))

    # Patch page parent references now that pages_id is known.
    patched_objects: list[bytes] = []
    for index, payload in enumerate(objects, start=1):
        if index in page_ids:
            payload = payload.replace(b"/Parent 0 0 R", f"/Parent {pages_id} 0 R".encode("utf-8"))
        patched_objects.append(payload)
    objects = patched_objects

    with open(output_path, "wb") as handle:
        handle.write(b"%PDF-1.4\n")
        offsets = [0]
        for index, payload in enumerate(objects, start=1):
            offsets.append(handle.tell())
            handle.write(f"{index} 0 obj\n".encode("utf-8"))
            handle.write(payload)
            handle.write(b"\nendobj\n")
        xref_offset = handle.tell()
        handle.write(f"xref\n0 {len(objects) + 1}\n".encode("utf-8"))
        handle.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            handle.write(f"{offset:010d} 00000 n \n".encode("utf-8"))
        handle.write(
            (
                "trailer\n"
                f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R /Info << /Title ({_escape_pdf_text(title[:80])}) /Author (Dhaka Bank DCIM) /Subject ({_escape_pdf_text(footer[:80])}) >> >>\n"
                f"startxref\n{xref_offset}\n%%EOF\n"
            ).encode("utf-8")
        )


def render_pdf(dataset: ReportDataset, context: GeneratorContext, output_path: str, filename: str, *, row_limit: int = 1000) -> RenderedArtifact:
    if not dataset.tables and not dataset.summary_rows:
        raise ValueError("The dataset does not contain any content to render.")

    tables = dataset.tables or []
    total_rows = sum(len(list(table.rows)) for table in tables if hasattr(table.rows, "__len__"))
    if total_rows and total_rows > row_limit:
        raise ValueError("PDF output is limited to smaller reports. Use CSV or XLSX for larger exports.")

    lines: list[str] = [
        dataset.title,
        dataset.subtitle or "",
        f"Organization: {getattr(context.organization, 'name', '')}",
        f"Data center: {getattr(context.data_center, 'name', 'All')}",
        f"Generated: {context.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Trigger source: {context.job.trigger_source}",
        "",
    ]
    for row in dataset.summary_rows:
        label = row.get("label") or row.get("key") or ""
        value = row.get("value", "")
        lines.append(f"{label}: {value}")
    if dataset.summary_rows:
        lines.append("")

    for table in tables:
        lines.append(table.title or table.name)
        lines.extend(_lines_for_table(table))
        lines.append("")

    if dataset.warnings:
        lines.append("Warnings")
        lines.extend(f"- {warning}" for warning in dataset.warnings)

    pages = _paginate_lines(lines, max_lines_per_page=38)
    _write_pdf(
        output_path,
        pages,
        title=dataset.title,
        footer="Confidential - Dhaka Bank DCIM report",
    )
    return _artifact_metadata(output_path, filename)

