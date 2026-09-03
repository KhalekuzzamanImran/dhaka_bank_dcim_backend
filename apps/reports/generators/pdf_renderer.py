from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .base import GeneratorContext, ReportDataset, ReportTable, RenderedArtifact
from .datasets import format_cell_value


@dataclass
class PageLayout:
    page_width: int = 595
    page_height: int = 842
    orientation: str = "portrait"
    target_line_width: int = 98
    table_font_size: float = 9.0
    table_leading: float = 11.0


def _resolve_page_layout(context: GeneratorContext, tables: list[ReportTable]) -> PageLayout:
    config = _get_template_config(context)
    orientation = str(config.get("pdf_orientation") or "portrait").strip().lower()
    page_size = str(config.get("pdf_page_size") or "A4").strip().upper()

    if page_size == "LETTER":
        base_w, base_h = 612, 792
    elif page_size == "A3":
        base_w, base_h = 842, 1191
    else:  # A4
        base_w, base_h = 595, 842

    is_landscape = (orientation == "landscape")
    if is_landscape:
        page_width, page_height = max(base_w, base_h), min(base_w, base_h)
        target_line_width = 145 if page_size != "A3" else 210
        table_font_size = 9.0
        table_leading = 11.0
    else:
        page_width, page_height = min(base_w, base_h), max(base_w, base_h)
        max_cols = max((len(t.columns) for t in tables), default=0)
        if max_cols >= 8:
            target_line_width = 135
            table_font_size = 6.5
            table_leading = 8.5
        elif max_cols >= 6:
            target_line_width = 118
            table_font_size = 7.5
            table_leading = 9.5
        else:
            target_line_width = 98
            table_font_size = 9.0
            table_leading = 11.0

    return PageLayout(
        page_width=page_width,
        page_height=page_height,
        orientation=orientation,
        target_line_width=target_line_width,
        table_font_size=table_font_size,
        table_leading=table_leading,
    )


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


def _safe_text(value) -> str:
    text = str(format_cell_value(value) if value is not None else "")
    return text.replace("\n", " ").replace("\r", " ").strip()


def _fit_text(value: str, width: int) -> str:
    if width <= 0:
        return ""
    text = _safe_text(value)
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


def _truncate_text(value: str, width: int) -> str:
    text = _safe_text(value)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


def _build_pretty_table_lines(table: ReportTable, *, max_width: int = 18, target_line_width: int = 100) -> list[str]:
    rows = [dict(row) for row in table.rows]
    columns = list(table.columns)
    if not columns:
        return [f"{table.title or table.name}", "(no columns)"]

    widths: list[int] = []
    for column in columns:
        header_width = len(_safe_text(column))
        value_width = header_width
        for row in rows:
            value_width = max(value_width, len(_safe_text(row.get(column))))
        widths.append(min(max(header_width, value_width), max_width))

    # PDF uses a monospaced font for dataset tables, so we can stretch the ASCII
    # table to the full usable page width instead of leaving narrow content-sized
    # columns. Keep a small safety margin so the border stays inside the page.
    usable_total_width = max(len(columns) * 4, target_line_width - (3 * len(columns) + 1))
    current_total_width = sum(widths)
    if current_total_width < usable_total_width:
        extra = usable_total_width - current_total_width
        for index in range(extra):
            widths[index % len(widths)] += 1
    elif current_total_width > usable_total_width:
        # Shrink the widest columns first, but never collapse any column entirely.
        overflow = current_total_width - usable_total_width
        minimum_width = 4
        while overflow > 0:
            adjustable = [index for index, width in enumerate(widths) if width > minimum_width]
            if not adjustable:
                break
            adjustable.sort(key=lambda index: widths[index], reverse=True)
            for index in adjustable:
                if overflow <= 0:
                    break
                shrink_by = min(widths[index] - minimum_width, max(1, overflow // len(adjustable) or 1))
                widths[index] -= shrink_by
                overflow -= shrink_by

    def border(left: str, fill: str, sep: str, right: str) -> str:
        parts = [fill * (width + 2) for width in widths]
        return f"{left}{sep.join(parts)}{right}"

    def row(values: list[str]) -> str:
        cells = [f" {_fit_text(value, width)} " for value, width in zip(values, widths)]
        return f"|{'|'.join(cells)}|"

    lines = [border("+", "-", "+", "+"), row(columns), border("+", "=", "+", "+")]
    if rows:
        for entry in rows:
            lines.append(row([_truncate_text(entry.get(column), width) for column, width in zip(columns, widths)]))
    else:
        placeholder = "No data available"
        span = max(1, sum(widths) + (3 * len(widths)) - 2)
        lines.append(f"| {placeholder.ljust(span - 2)} |")
    lines.append(border("+", "-", "+", "+"))
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


def _get_template_config(context: GeneratorContext) -> dict:
    template_snapshot = context.template_snapshot if isinstance(context.template_snapshot, dict) else {}
    config = template_snapshot.get("configuration")
    return config if isinstance(config, dict) else {}


def _as_bool(value, default=False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _format_timestamp(value: datetime | None, timezone_name: str | None = None) -> str | None:
    if value is None:
        return None
    if timezone_name:
        try:
            tz = ZoneInfo(timezone_name)
            value = value.astimezone(tz)
        except Exception:
            pass
    return value.strftime("%d %b %Y, %H:%M")


def _format_date_or_datetime(value, tz=None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        parsed_dt = parse_datetime(value)
        if parsed_dt is not None:
            value = parsed_dt
        else:
            parsed_d = parse_date(value)
            if parsed_d is not None:
                return parsed_d.strftime("%Y-%m-%d")
            return value

    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, datetime):
        try:
            if timezone.is_naive(value):
                value = timezone.make_aware(value, tz or timezone.get_current_timezone())
            elif tz is not None:
                value = value.astimezone(tz)
            else:
                value = timezone.localtime(value)
        except Exception:
            pass
        if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")

    return str(value)


def _device_scope_label(context: GeneratorContext, dataset: ReportDataset | None = None) -> str | None:
    scope_snapshot = context.scope_snapshot if isinstance(context.scope_snapshot, dict) else {}
    selected_devices = scope_snapshot.get("selected_devices") if isinstance(scope_snapshot, dict) else []
    config = _get_template_config(context)
    device_scope = config.get("device_scope")
    device_type = config.get("device_type")

    names = []
    if isinstance(selected_devices, list) and selected_devices:
        for d in selected_devices:
            if isinstance(d, dict):
                name = d.get("name") or d.get("code")
                if name and str(name) not in names:
                    names.append(str(name))
            elif hasattr(d, "name"):
                name = str(d.name)
                if name and name not in names:
                    names.append(name)

    if dataset and dataset.tables:
        table_devices = []
        for table in dataset.tables:
            for row in (table.rows or []):
                if isinstance(row, dict) and row.get("device"):
                    dev_name = str(row["device"]).strip()
                    if dev_name and dev_name not in table_devices:
                        table_devices.append(dev_name)
        if table_devices and len(table_devices) > len(names):
            names = table_devices

    if names:
        if len(names) == 1:
            return names[0]
        elif len(names) <= 4:
            return ", ".join(names)
        else:
            if device_scope == "device_type" and device_type:
                return f"All {device_type} ({len(names)} devices)"
            elif device_scope == "all_devices":
                return f"All Devices ({len(names)} devices)"
            return f"{', '.join(names[:3])} (+{len(names) - 3} more)"

    if device_scope == "all_devices":
        return "All Devices"
    elif device_scope == "device_type" and device_type:
        return f"All {device_type}"

    params = context.parameters if isinstance(context.parameters, dict) else {}
    device_id = params.get("device_id")
    if device_id:
        return str(device_id)
    return None


def _date_range_label(context: GeneratorContext) -> str | None:
    params = context.parameters if isinstance(context.parameters, dict) else {}
    start_value = params.get("date_from") or params.get("start_date")
    end_value = params.get("date_to") or params.get("end_date")
    if not start_value and not end_value:
        return None
    tz = getattr(context, "timezone", None) or timezone.get_current_timezone()
    start_str = _format_date_or_datetime(start_value, tz=tz)
    end_str = _format_date_or_datetime(end_value, tz=tz)
    if start_str and end_str:
        return f"{start_str} to {end_str}"
    return start_str or end_str


def _build_header_lines(context: GeneratorContext, dataset: ReportDataset) -> list[str]:
    config = _get_template_config(context)
    header_config = config.get("report_header") if isinstance(config.get("report_header"), dict) else {}
    if not _as_bool(header_config.get("enabled"), True):
        return []

    title = str(header_config.get("title") or dataset.title or "").strip()
    subtitle = str(header_config.get("subtitle") or dataset.subtitle or "").strip()
    lines: list[str] = []
    if title and _as_bool(header_config.get("show_title"), True):
        lines.append(title)
    if subtitle and _as_bool(header_config.get("show_subtitle"), True):
        lines.append(subtitle)

    if _as_bool(header_config.get("show_organization"), True):
        organization_name = getattr(context.organization, "name", None)
        if organization_name:
            lines.append(f"Organization: {organization_name}")
    if _as_bool(header_config.get("show_data_center"), True):
        data_center_name = getattr(context.data_center, "name", None) or "All"
        lines.append(f"Data center: {data_center_name}")
    if _as_bool(header_config.get("show_device"), False):
        device_label = _device_scope_label(context, dataset)
        if device_label:
            lines.append(f"Device: {device_label}")
    if _as_bool(header_config.get("show_date_range"), True):
        date_range = _date_range_label(context)
        if date_range:
            lines.append(f"Date range: {date_range}")
    if _as_bool(header_config.get("show_generated_at"), True):
        lines.append(f"Generated at: {context.generated_at.strftime('%d %b %Y, %H:%M')}")
    if _as_bool(header_config.get("show_generated_by"), False):
        generated_by = getattr(getattr(context.job, "requested_by", None), "full_name", None) or getattr(getattr(context.job, "requested_by", None), "username", None)
        if generated_by:
            lines.append(f"Generated by: {generated_by}")
    if _as_bool(header_config.get("show_timezone"), False):
        tz_name = getattr(context.timezone, "key", None) or getattr(context.timezone, "zone", None) or str(context.timezone)
        if tz_name:
            lines.append(f"Timezone: {tz_name}")
    return lines


def _build_header_banner(context: GeneratorContext) -> dict | None:
    config = _get_template_config(context)
    header_config = config.get("report_header") if isinstance(config.get("report_header"), dict) else {}
    banner_config = header_config.get("brand_banner") if isinstance(header_config.get("brand_banner"), dict) else {}
    if not _as_bool(banner_config.get("enabled"), True):
        return None

    organization_name = getattr(context.organization, "name", None) or "Dhaka Bank"
    label = str(banner_config.get("label") or f"{organization_name} DCIM").strip()
    subtitle = str(banner_config.get("subtitle") or "Operational reporting").strip()
    badge = str(banner_config.get("badge") or "DB").strip()[:3].upper() or "DB"
    return {
        "label": label,
        "subtitle": subtitle,
        "badge": badge,
    }


def _build_footer_lines(context: GeneratorContext) -> list[str]:
    config = _get_template_config(context)
    footer_config = config.get("report_footer") if isinstance(config.get("report_footer"), dict) else {}
    if not _as_bool(footer_config.get("enabled"), True):
        return []

    lines: list[str] = []
    custom_text = str(footer_config.get("custom_text") or "").strip()
    if custom_text:
        lines.append(custom_text)
    if _as_bool(footer_config.get("show_confidentiality_note"), True):
        lines.append("Confidential - Dhaka Bank DCIM report")
    if _as_bool(footer_config.get("show_generated_at"), False):
        lines.append(f"Generated at: {context.generated_at.strftime('%d %b %Y, %H:%M')}")
    if _as_bool(footer_config.get("show_timezone"), False):
        tz_name = getattr(context.timezone, "key", None) or getattr(context.timezone, "zone", None) or str(context.timezone)
        if tz_name:
            lines.append(f"Timezone: {tz_name}")
    return lines


def _build_page_stream(
    banner: dict | None,
    header_lines: list[str],
    body_lines: list[str],
    footer_lines: list[str],
    *,
    layout: PageLayout,
    include_header: bool,
    page_number: int,
    total_pages: int,
) -> bytes:
    y = layout.page_height - 36
    margin_x = 24
    usable_width = layout.page_width - (2 * margin_x)
    commands = [
        "BT",
    ]

    def add_text(text: str, *, size: float, x: float, y_pos: float, color: tuple[float, float, float] | None = None, font: str = "F1"):
        if color is not None:
            commands.append(f"{color[0]} {color[1]} {color[2]} rg")
        commands.append(f"/{font} {size} Tf")
        commands.append(f"1 0 0 1 {x} {y_pos} Tm")
        commands.append(f"({_escape_pdf_text(text)}) Tj")

    def add_line(text: str, *, size: float, leading: float, x: float = 24, font: str = "F1"):
        nonlocal y
        add_text(text, size=size, x=x, y_pos=y, color=(0, 0, 0), font=font)
        y -= leading

    if include_header and banner:
        banner_y = layout.page_height - 72
        commands.append("0.94 0.97 1 rg")
        commands.append(f"{margin_x} {banner_y} {usable_width} 24 re")
        commands.append("f")
        commands.append("0.00 0.45 0.85 rg")
        commands.append(f"{margin_x} {banner_y} 34 24 re")
        commands.append("f")
        add_text(str(banner.get("badge") or "DB"), size=9, x=margin_x + 7, y_pos=banner_y + 9, color=(1, 1, 1), font="F3")
        add_text(str(banner.get("label") or "Dhaka Bank DCIM"), size=11, x=margin_x + 48, y_pos=banner_y + 16, color=(0.08, 0.12, 0.2), font="F3")
        add_text(str(banner.get("subtitle") or "Operational reporting"), size=8, x=margin_x + 48, y_pos=banner_y + 5, color=(0.38, 0.42, 0.5))
        y = banner_y - 18

    if include_header and header_lines:
        add_line(header_lines[0], size=14, leading=18, font="F3")
        for line in header_lines[1:]:
            add_line(line, size=9, leading=12, font="F3")
        y -= 4

    min_body_y = 68
    for line in body_lines:
        if line.startswith("+") or line.startswith("|") or line.startswith("-"):
            add_line(line, size=layout.table_font_size, leading=layout.table_leading, font="F2")
        else:
            add_line(line, size=10, leading=12, font="F3")
        if y <= min_body_y:
            break

    rule_y = 48
    rule_end = layout.page_width - margin_x
    commands.append("0.80 0.84 0.90 RG")
    commands.append(f"{margin_x} {rule_y} m {rule_end} {rule_y} l S")

    footer_y = 34
    if footer_lines:
        for line in footer_lines[:2]:
            add_text(line, size=7, x=margin_x, y_pos=footer_y, color=(0.25, 0.28, 0.34), font="F1")
            footer_y -= 10

    page_num_x = layout.page_width - margin_x - 80
    add_text(f"Page {page_number} of {total_pages}", size=8, x=page_num_x, y_pos=18, color=(0.25, 0.28, 0.34), font="F1")
    commands.append("ET")
    content = "\n".join(commands).encode("utf-8")
    return content


def _write_pdf(
    output_path: str,
    pages: list[tuple[dict | None, list[str], list[str], list[str], bool]],
    title: str,
    footer: str,
    layout: PageLayout,
) -> None:
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold_font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    mono_font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    page_ids = []
    for page_index, (banner, header_lines, body_lines, footer_lines, include_header) in enumerate(pages, start=1):
        stream = _build_page_stream(
            banner,
            header_lines,
            body_lines,
            footer_lines,
            layout=layout,
            include_header=include_header,
            page_number=page_index,
            total_pages=len(pages),
        )
        content_id = add_object(f"<< /Length {len(stream)} >>\nstream\n".encode("utf-8") + stream + b"\nendstream")
        page_id = add_object(
            (
                f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 {layout.page_width} {layout.page_height}] "
                f"/Resources << /Font << /F1 {font_id} 0 R /F2 {mono_font_id} 0 R /F3 {bold_font_id} 0 R >> >> /Contents {content_id} 0 R >>"
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

    tables = [
        ReportTable(
            name=table.name,
            columns=list(table.columns),
            rows=[dict(row) for row in table.rows],
            title=table.title,
            description=table.description,
            primary=table.primary,
            include_in_csv=table.include_in_csv,
        )
        for table in (dataset.tables or [])
    ]
    total_rows = sum(len(table.rows) for table in tables)
    if row_limit and total_rows and total_rows > row_limit:
        raise ValueError("PDF output is limited to smaller reports. Use CSV or XLSX for larger exports.")

    layout = _resolve_page_layout(context, tables)
    header_lines = _build_header_lines(context, dataset)
    header_banner = _build_header_banner(context)
    footer_lines = _build_footer_lines(context)
    body_lines: list[str] = []
    for row in dataset.summary_rows:
        label = row.get("label") or row.get("key") or ""
        value = row.get("value", "")
        body_lines.append(f"{label}: {value}")
    if dataset.summary_rows:
        body_lines.append("")

    for table in tables:
        if table.title or table.name:
            body_lines.append(str(table.title or table.name))
        body_lines.extend(_build_pretty_table_lines(table, target_line_width=layout.target_line_width))
        body_lines.append("")

    if dataset.warnings:
        body_lines.append("Warnings")
        body_lines.extend(f"- {warning}" for warning in dataset.warnings)

    first_page_limit = max(18, int((layout.page_height - 180 - len(header_lines) * 12) / layout.table_leading))
    remaining_limit = max(25, int((layout.page_height - 90) / layout.table_leading))
    body_pages: list[list[str]] = []
    remaining_lines = list(body_lines)
    if remaining_lines:
        body_pages.append(remaining_lines[:first_page_limit])
        remaining_lines = remaining_lines[first_page_limit:]
    while remaining_lines:
        body_pages.append(remaining_lines[:remaining_limit])
        remaining_lines = remaining_lines[remaining_limit:]
    if not body_pages:
        body_pages = [[]]
    pages = [
        (header_banner if index == 0 else None, header_lines if index == 0 else [], page_lines, footer_lines, index == 0)
        for index, page_lines in enumerate(body_pages)
    ]
    _write_pdf(
        output_path,
        pages,
        title=dataset.title,
        footer="Confidential - Dhaka Bank DCIM report",
        layout=layout,
    )
    return _artifact_metadata(output_path, filename)
