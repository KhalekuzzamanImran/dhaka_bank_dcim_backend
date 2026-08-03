from __future__ import annotations

import hashlib
import os
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

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


def _cell_xml(column_index: int, row_index: int, value) -> str:
    cell_ref = f"{_column_letter(column_index)}{row_index}"
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return f'<c r="{cell_ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{cell_ref}"><v>{value}</v></c>'
    if isinstance(value, datetime):
        return f'<c r="{cell_ref}" s="1"><v>{_excel_serial(value)}</v></c>'
    text = _escape_formula_injection(str(value))
    return f'<c r="{cell_ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _render_sheet(table: ReportTable) -> str:
    rows_xml = []
    header_row = "".join(_cell_xml(idx, 1, column) for idx, column in enumerate(table.columns, start=1))
    rows_xml.append(f'<row r="1">{header_row}</row>')
    max_widths = [len(str(column)) for column in table.columns]
    row_index = 2
    for row in table.rows:
        cells = []
        for idx, column in enumerate(table.columns, start=1):
            value = row.get(column)
            if value is None:
                value = ""
            if isinstance(value, str) and len(value) > max_widths[idx - 1]:
                max_widths[idx - 1] = len(value)
            cells.append(_cell_xml(idx, row_index, value))
        rows_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        row_index += 1

    auto_filter_ref = f"A1:{_column_letter(len(table.columns))}{max(1, row_index - 1)}"
    cols_xml = "".join(
        f'<col min="{idx}" max="{idx}" width="{min(max(width + 2, 12), 48)}" customWidth="1"/>'
        for idx, width in enumerate(max_widths, start=1)
    )
    sheet_name = _safe_sheet_name(table.title or table.name)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft" activeCell="A2" sqref="A2"/>
    </sheetView>
  </sheetViews>
  <dimension ref="A1:{_column_letter(len(table.columns))}{max(1, row_index - 1)}"/>
  <sheetFormatPr defaultRowHeight="15"/>
  <cols>{cols_xml}</cols>
  <sheetData>{''.join(rows_xml)}</sheetData>
  <autoFilter ref="{auto_filter_ref}"/>
</worksheet>
"""


def render_xlsx(dataset: ReportDataset, context: GeneratorContext, output_path: str, filename: str) -> RenderedArtifact:
    tables = [table for table in dataset.tables if table.rows]
    if not tables:
        tables = [dataset.primary_table] if dataset.primary_table else []
    if not tables:
        raise ValueError("The dataset does not contain any table to render.")

    summary_table = ReportTable(
        name="Summary",
        title="Summary",
        columns=["label", "value"],
        rows=dataset.summary_rows or [{"label": "status", "value": "No summary available"}],
        primary=False,
    )
    sheets = [summary_table, *tables]
    sheet_xml = [_render_sheet(table) for table in sheets]
    safe_names = [_safe_sheet_name(table.title or table.name) for table in sheets]

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
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  </cellXfs>
  <numFmts count="1">
    <numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/>
  </numFmts>
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
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
  <Application>Dhaka Bank DCIM</Application>
</Properties>
""",
        )
        for index, xml in enumerate(sheet_xml, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)

    return _artifact_metadata(output_path, filename)
