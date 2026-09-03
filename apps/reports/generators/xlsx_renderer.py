from __future__ import annotations

import hashlib
import os
import re
import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .base import GeneratorContext, ReportDataset, ReportTable, RenderedArtifact

_EXCEL_EPOCH = datetime(1899, 12, 30)


def _artifact_metadata(path: str, filename: str) -> RenderedArtifact:
    size_bytes = os.path.getsize(path)
    sha256 = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            sha256.update(chunk)
    return RenderedArtifact(
        format="XLSX",
        path=path,
        filename=filename,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=size_bytes,
        checksum_sha256=sha256.hexdigest(),
    )


def _safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]\:\*\?\/\\]", " ", str(name or "").strip())[:31].strip()
    return cleaned or "Sheet"


def _column_letter(index: int) -> str:
    result = ""
    current = index
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def _excel_serial(value: datetime) -> float:
    delta = value.replace(tzinfo=None) - _EXCEL_EPOCH
    return delta.days + (delta.seconds / 86400) + (delta.microseconds / 86400000000)


def _escape_formula_injection(value: str) -> str:
    if value and value[0] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _cell_xml(column_index: int, row_index: int, value, *, style_index: int = 0) -> str:
    cell_ref = f"{_column_letter(column_index)}{row_index}"
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        style_attr = f' s="{style_index}"' if style_index else ""
        return f'<c r="{cell_ref}"{style_attr} t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        style_attr = f' s="{style_index}"' if style_index else ""
        return f'<c r="{cell_ref}"{style_attr}><v>{value}</v></c>'
    if isinstance(value, datetime):
        return f'<c r="{cell_ref}" s="{style_index or 1}"><v>{_excel_serial(value)}</v></c>'
    text = _escape_formula_injection(str(value))
    style_attr = f' s="{style_index}"' if style_index else ""
    return f'<c r="{cell_ref}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _template_config(context: GeneratorContext) -> dict:
    template_snapshot = context.template_snapshot if isinstance(context.template_snapshot, dict) else {}
    configuration = template_snapshot.get("configuration")
    return configuration if isinstance(configuration, dict) else {}


def _as_bool(value, default=False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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
    config = _template_config(context)
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


def _report_info_rows(context: GeneratorContext, dataset: ReportDataset | None = None) -> list[dict[str, str]]:
    config = _template_config(context)
    header_config = config.get("report_header") if isinstance(config.get("report_header"), dict) else {}
    footer_config = config.get("report_footer") if isinstance(config.get("report_footer"), dict) else {}
    banner_config = header_config.get("brand_banner") if isinstance(header_config.get("brand_banner"), dict) else {}

    job = getattr(context, "job", None)
    job_template_snapshot = getattr(job, "template_snapshot", {}) if isinstance(getattr(job, "template_snapshot", None), dict) else {}
    def_code = getattr(context.definition, "code", None) if getattr(context, "definition", None) else "--"
    dataset_title = dataset.title if dataset else ""
    dataset_sub = dataset.subtitle if dataset else ""
    output_cfg = context.output_config_snapshot if isinstance(getattr(context, "output_config_snapshot", None), dict) else {}

    rows: list[dict[str, str]] = [
        {"section": "banner", "label": "label", "value": str(banner_config.get("label") or getattr(context.organization, "name", None) or "Dhaka Bank DCIM")},
        {"section": "banner", "label": "subtitle", "value": str(banner_config.get("subtitle") or "Operational reporting")},
        {"section": "report", "label": "title", "value": str(header_config.get("title") or job_template_snapshot.get("name") or def_code or dataset_title or "Report Export") if _as_bool(header_config.get("show_title"), True) else "--"},
        {"section": "report", "label": "subtitle", "value": str(header_config.get("subtitle") or dataset_sub or "--") if _as_bool(header_config.get("show_subtitle"), True) else "--"},
        {"section": "report", "label": "type", "value": str(def_code or "--")},
        {"section": "report", "label": "output_format", "value": str(output_cfg.get("primary_format") or output_cfg.get("output_format") or "--")},
        {"section": "report", "label": "generated_at", "value": context.generated_at.strftime("%d %b %Y, %H:%M") if hasattr(context, "generated_at") and context.generated_at else "--"},
        {"section": "scope", "label": "organization", "value": getattr(context.organization, "name", None) or "--"},
        {"section": "scope", "label": "data_center", "value": getattr(context.data_center, "name", None) or "All"},
    ]

    device_label = _device_scope_label(context, dataset)
    if device_label:
        rows.append({"section": "scope", "label": "device", "value": str(device_label)})

    date_range = _date_range_label(context)
    if date_range:
        rows.append({"section": "scope", "label": "date_range", "value": date_range})

    if _as_bool(header_config.get("enabled"), True):
        rows.extend(
            [
                {"section": "header", "label": "subtitle", "value": str(header_config.get("subtitle") or "") or "--"},
                {"section": "header", "label": "show_organization", "value": "TRUE" if _as_bool(header_config.get("show_organization"), True) else "FALSE"},
                {"section": "header", "label": "show_data_center", "value": "TRUE" if _as_bool(header_config.get("show_data_center"), True) else "FALSE"},
                {"section": "header", "label": "show_device", "value": "TRUE" if _as_bool(header_config.get("show_device"), False) else "FALSE"},
                {"section": "header", "label": "show_date_range", "value": "TRUE" if _as_bool(header_config.get("show_date_range"), True) else "FALSE"},
                {"section": "header", "label": "show_generated_at", "value": "TRUE" if _as_bool(header_config.get("show_generated_at"), True) else "FALSE"},
                {"section": "header", "label": "show_generated_by", "value": "TRUE" if _as_bool(header_config.get("show_generated_by"), False) else "FALSE"},
            ]
        )

    if _as_bool(footer_config.get("enabled"), True):
        rows.extend(
            [
                {"section": "footer", "label": "text", "value": str(footer_config.get("custom_text") or "") or "--"},
                {"section": "footer", "label": "show_confidentiality_note", "value": "TRUE" if _as_bool(footer_config.get("show_confidentiality_note"), True) else "FALSE"},
                {"section": "footer", "label": "show_generated_at", "value": "TRUE" if _as_bool(footer_config.get("show_generated_at"), False) else "FALSE"},
                {"section": "footer", "label": "show_timezone", "value": "TRUE" if _as_bool(footer_config.get("show_timezone"), False) else "FALSE"},
                {"section": "footer", "label": "show_page_number", "value": "TRUE" if _as_bool(footer_config.get("show_page_number"), True) else "FALSE"},
            ]
        )

    return rows


def _render_sheet(
    table: ReportTable,
    context: GeneratorContext,
    dataset: ReportDataset,
    *,
    is_primary: bool = False,
    freeze_header: bool = True,
    auto_filter: bool = True,
    auto_size: bool = True,
) -> str:
    rows_xml = []
    max_widths = [len(str(column)) for column in table.columns] if table.columns else [15]
    row_index = 1

    header_config = _template_config(context).get("report_header") if isinstance(_template_config(context).get("report_header"), dict) else {}
    banner_config = header_config.get("brand_banner") if isinstance(header_config.get("brand_banner"), dict) else {}
    header_enabled = _as_bool(header_config.get("enabled"), True)

    num_cols = max(1, len(table.columns))
    last_col = _column_letter(num_cols)

    if is_primary and header_enabled:
        # Helper to fill remaining columns in a header row with clean white background
        def pad_header_row(current_row: int, start_col: int = 1, style: int = 8) -> str:
            cells = []
            for c_idx in range(start_col, num_cols + 1):
                col_let = _column_letter(c_idx)
                cells.append(f'<c r="{col_let}{current_row}" s="{style}"/>')
            return "".join(cells)

        # 1. Brand Banner
        if _as_bool(banner_config.get("enabled"), True):
            badge = str(banner_config.get("badge") or "DB").strip()[:3]
            brand_label = str(banner_config.get("label") or getattr(context.organization, "name", None) or "Dhaka Bank DCIM").strip()
            subtitle = str(banner_config.get("subtitle") or "Operational reporting").strip()
            banner_text = f"[{badge}]  {brand_label} — {subtitle}"
            banner_cells = f'<c r="A{row_index}" s="3" t="inlineStr"><is><t>{escape(banner_text)}</t></is></c>' + pad_header_row(row_index, 2, style=3)
            rows_xml.append(f'<row r="{row_index}" ht="26" customHeight="1">{banner_cells}</row>')
            if len(banner_text) > max_widths[0]:
                max_widths[0] = min(len(banner_text), 38)
            row_index += 1

        # 2. Report Title
        job_template_snap = getattr(getattr(context, "job", None), "template_snapshot", {}) if isinstance(getattr(getattr(context, "job", None), "template_snapshot", None), dict) else {}
        title = str(header_config.get("title") or job_template_snap.get("name") or getattr(context.definition, "code", None) or dataset.title or "Report Export").strip()
        if title and _as_bool(header_config.get("show_title"), True):
            title_cells = f'<c r="A{row_index}" s="4" t="inlineStr"><is><t>{escape(title)}</t></is></c>' + pad_header_row(row_index, 2, style=8)
            rows_xml.append(f'<row r="{row_index}" ht="22" customHeight="1">{title_cells}</row>')
            if len(title) > max_widths[0]:
                max_widths[0] = min(len(title), 38)
            row_index += 1

        # 3. Subtitle
        sub = str(header_config.get("subtitle") or dataset.subtitle or "").strip()
        if sub and _as_bool(header_config.get("show_subtitle"), True):
            sub_cells = f'<c r="A{row_index}" s="5" t="inlineStr"><is><t>{escape(sub)}</t></is></c>' + pad_header_row(row_index, 2, style=8)
            rows_xml.append(f'<row r="{row_index}" ht="18" customHeight="1">{sub_cells}</row>')
            if len(sub) > max_widths[0]:
                max_widths[0] = min(len(sub), 38)
            row_index += 1

        # 4. Metadata key-value rows
        meta_items = []
        if _as_bool(header_config.get("show_organization"), True):
            org = getattr(context.organization, "name", None) or "Dhaka Bank"
            meta_items.append(("Organization", org))

        if _as_bool(header_config.get("show_data_center"), True):
            dc = getattr(context.data_center, "name", None) or "All"
            meta_items.append(("Data center", dc))

        if _as_bool(header_config.get("show_device"), True):
            dev = _device_scope_label(context, dataset)
            if dev:
                meta_items.append(("Device", dev))

        if _as_bool(header_config.get("show_date_range"), True):
            dr = _date_range_label(context)
            if dr:
                meta_items.append(("Date range", dr))

        if _as_bool(header_config.get("show_generated_at"), True):
            meta_items.append(("Generated at", context.generated_at.strftime("%d %b %Y, %H:%M") if hasattr(context, "generated_at") and context.generated_at else "--"))

        if _as_bool(header_config.get("show_generated_by"), True):
            gen_by = getattr(getattr(context.job, "requested_by", None), "full_name", None) or getattr(getattr(context.job, "requested_by", None), "username", None)
            if gen_by:
                meta_items.append(("Generated by", gen_by))

        if _as_bool(header_config.get("show_timezone"), False):
            tz_name = getattr(context.timezone, "key", None) or getattr(context.timezone, "zone", None) or str(context.timezone)
            if tz_name:
                meta_items.append(("Timezone", tz_name))

        for label, val in meta_items:
            cell_a = f'<c r="A{row_index}" s="6" t="inlineStr"><is><t>{escape(label)}:</t></is></c>'
            cell_b = f'<c r="B{row_index}" s="7" t="inlineStr"><is><t>{escape(str(val))}</t></is></c>' if num_cols > 1 else ""
            remaining = pad_header_row(row_index, 3 if num_cols > 1 else 2, style=8)
            rows_xml.append(f'<row r="{row_index}" ht="18">{cell_a}{cell_b}{remaining}</row>')
            if len(str(label)) + 2 > max_widths[0]:
                max_widths[0] = len(str(label)) + 2
            if num_cols > 1 and len(str(val)) > max_widths[1]:
                max_widths[1] = len(str(val))
            row_index += 1

        # Clean spacer row before table (with white background across all columns)
        spacer_cells = pad_header_row(row_index, 1, style=8)
        rows_xml.append(f'<row r="{row_index}" ht="14">{spacer_cells}</row>')
        row_index += 1

    header_table_row = row_index
    header_cells = "".join(_cell_xml(idx, header_table_row, column, style_index=2) for idx, column in enumerate(table.columns, start=1))
    rows_xml.append(f'<row r="{header_table_row}" ht="24" customHeight="1">{header_cells}</row>')
    row_index += 1

    for row in table.rows:
        cells = []
        row_style = 3 if table.name == "Report Info" and row.get("section") in {"banner", "report"} else 0
        for idx, column in enumerate(table.columns, start=1):
            value = row.get(column)
            if value is None:
                value = ""
            val_str = str(value)
            if len(val_str) > max_widths[idx - 1]:
                max_widths[idx - 1] = len(val_str)
            cells.append(_cell_xml(idx, row_index, value, style_index=row_style))
        rows_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        row_index += 1

    last_row = max(1, row_index - 1)
    dimension_ref = f"A1:{last_col}{last_row}"

    # OpenXML requires <dimension> before <sheetViews>
    sheet_views = ""
    if freeze_header:
        sheet_views = f"""  <sheetViews>
    <sheetView tabSelected="1" workbookViewId="0">
      <pane ySplit="{header_table_row}" topLeftCell="A{header_table_row + 1}" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft" activeCell="A{header_table_row + 1}" sqref="A{header_table_row + 1}"/>
    </sheetView>
  </sheetViews>"""
    else:
        sheet_views = """  <sheetViews>
    <sheetView tabSelected="1" workbookViewId="0"/>
  </sheetViews>"""

    cols_xml = ""
    if auto_size and table.columns:
        cols_parts = [
            f'<col min="{idx}" max="{idx}" width="{min(max(width + 3, 14), 50)}" customWidth="1"/>'
            for idx, width in enumerate(max_widths, start=1)
        ]
        cols_xml = f"\n  <cols>\n    {''.join(cols_parts)}\n  </cols>"

    auto_filter_xml = ""
    if auto_filter and last_row > header_table_row and table.columns:
        auto_filter_xml = f'\n  <autoFilter ref="A{header_table_row}:{last_col}{last_row}"/>'

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="{dimension_ref}"/>
{sheet_views}
  <sheetFormatPr defaultRowHeight="15"/>{cols_xml}
  <sheetData>
    {''.join(rows_xml)}
  </sheetData>{auto_filter_xml}
</worksheet>
"""


def render_xlsx(dataset: ReportDataset, context: GeneratorContext, output_path: str, filename: str) -> RenderedArtifact:
    config = _template_config(context)
    custom_sheet_name = str(config.get("xlsx_sheet_name") or "").strip()
    freeze_header = _as_bool(config.get("xlsx_freeze_header"), True)
    auto_filter = _as_bool(config.get("xlsx_auto_filter"), True)
    auto_size = _as_bool(config.get("xlsx_auto_size"), True)
    include_summary = _as_bool(config.get("xlsx_include_summary"), True)

    data_tables = [
        ReportTable(
            name=table.name,
            columns=list(table.columns),
            rows=[dict(r) for r in table.rows],
            title=table.title,
            description=table.description,
            primary=table.primary,
            include_in_csv=table.include_in_csv,
        )
        for table in dataset.tables
        if table.rows
    ]
    if not data_tables and dataset.primary_table:
        data_tables = [
            ReportTable(
                name=dataset.primary_table.name,
                columns=list(dataset.primary_table.columns),
                rows=[dict(r) for r in dataset.primary_table.rows],
                title=dataset.primary_table.title,
                description=dataset.primary_table.description,
                primary=dataset.primary_table.primary,
                include_in_csv=dataset.primary_table.include_in_csv,
            )
        ]
    if not data_tables:
        raise ValueError("The dataset does not contain any table to render.")

    # Apply custom sheet name to primary data table
    primary_table = data_tables[0]
    if custom_sheet_name:
        primary_table.title = custom_sheet_name
        primary_table.name = custom_sheet_name

    sheets: list[tuple[ReportTable, bool]] = []
    # Primary data sheets are always first
    for idx, table in enumerate(data_tables):
        sheets.append((table, idx == 0))

    if include_summary:
        summary_table = ReportTable(
            name="Summary",
            title="Summary",
            columns=["label", "value"],
            rows=dataset.summary_rows or [{"label": "status", "value": "No summary available"}],
            primary=False,
        )
        info_table = ReportTable(
            name="Report Info",
            title="Report Info",
            columns=["section", "label", "value"],
            rows=_report_info_rows(context, dataset),
            primary=False,
        )
        sheets.append((summary_table, False))
        sheets.append((info_table, False))

    sheet_xml = [
        _render_sheet(
            table,
            context,
            dataset,
            is_primary=is_prim,
            freeze_header=freeze_header,
            auto_filter=auto_filter,
            auto_size=auto_size,
        )
        for table, is_prim in sheets
    ]
    safe_names = []
    for table, _ in sheets:
        base_name = _safe_sheet_name(table.title or table.name)
        candidate = base_name
        count = 2
        while candidate in safe_names:
            candidate = f"{base_name[:28]}_{count}"
            count += 1
        safe_names.append(candidate)

    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(safe_names, start=1)
    )
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for idx in range(1, len(sheets) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{workbook_sheets}</sheets>
</workbook>
""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
""" + "".join(
                f'  <Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>\n'
                for idx in range(1, len(sheets) + 1)
            ) + """  <Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1">
    <numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/>
  </numFmts>
  <fonts count="8">
    <font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><color rgb="FF003366"/><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><color rgb="FF003366"/><sz val="14"/><name val="Calibri"/><family val="2"/></font>
    <font><i/><color rgb="FF4B5563"/><sz val="10"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><color rgb="FF374151"/><sz val="10"/><name val="Calibri"/><family val="2"/></font>
    <font><color rgb="FF1F2937"/><sz val="10"/><name val="Calibri"/><family val="2"/></font>
  </fonts>
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF0073FF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE8F1FF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFFFFF"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFD1D5DB"/></left>
      <right style="thin"><color rgb="FFD1D5DB"/></right>
      <top style="thin"><color rgb="FFD1D5DB"/></top>
      <bottom style="thin"><color rgb="FFD1D5DB"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="9">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="4" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="5" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="6" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="7" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>
  </cellXfs>
</styleSheet>
""",
        )
        archive.writestr(
            "docProps/core.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Report Export</dc:title>
  <dc:creator>Dhaka Bank DCIM</dc:creator>
  <cp:lastModifiedBy>Dhaka Bank DCIM</cp:lastModifiedBy>
</cp:coreProperties>
""",
        )
        archive.writestr(
            "docProps/app.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Dhaka Bank DCIM</Application>
  <DocSecurity>0</DocSecurity>
  <ScaleCrop>false</ScaleCrop>
  <HeadingPairs>
    <vt:vector size="2" baseType="variant">
      <vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>
      <vt:variant><vt:i4>{len(sheets)}</vt:i4></vt:variant>
    </vt:vector>
  </HeadingPairs>
  <TitlesOfParts>
    <vt:vector size="{len(sheets)}" baseType="lpstr">
      {"".join(f"<vt:lpstr>{escape(name)}</vt:lpstr>" for name in safe_names)}
    </vt:vector>
  </TitlesOfParts>
  <Company>Dhaka Bank</Company>
  <LinksUpToDate>false</LinksUpToDate>
  <SharedDoc>false</SharedDoc>
  <HyperlinksChanged>false</HyperlinksChanged>
  <AppVersion>16.0300</AppVersion>
</Properties>
""",
        )
        for index, xml in enumerate(sheet_xml, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)

    return _artifact_metadata(output_path, filename)
