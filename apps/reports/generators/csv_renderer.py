from __future__ import annotations

import csv
import hashlib
import os
from datetime import date, datetime

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

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


def _as_bool(value, default=False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_delimiter(delimiter: str | None) -> str:
    if not delimiter:
        return ","
    raw_delimit = str(delimiter)
    clean_delimit = raw_delimit.strip().lower()
    if raw_delimit == "\t" or clean_delimit in ("\\t", "tab"):
        return "\t"
    if raw_delimit.strip() in (",", ";", "|", ":"):
        return raw_delimit.strip()
    return ","


def _normalize_encoding(encoding: str | None) -> str:
    if not encoding:
        return "utf-8-sig"
    enc = str(encoding).strip().lower().replace("_", "-")
    if enc in ("utf-8", "utf8"):
        # Include a BOM so Excel and similar spreadsheet applications detect
        # UTF-8 correctly instead of rendering characters such as ° as Â°.
        return "utf-8-sig"
    elif enc in ("utf-8-sig", "utf8-sig", "utf-8 with bom"):
        return "utf-8-sig"
    elif enc in ("utf-16", "utf16"):
        return "utf-16"
    elif enc in ("latin-1", "latin1", "iso-8859-1"):
        return "iso-8859-1"
    elif enc in ("ascii",):
        return "ascii"
    return "utf-8"


def _display_column_name(column: str) -> str:
    return str(column or "").replace("_", " ").strip().lower().capitalize()


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
                return parsed_d.strftime("%d %b %Y")
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
            return value.strftime("%d %b %Y")
        return value.strftime("%d %b %Y, %H:%M")

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
        return f"{start_str} – {end_str}"
    return start_str or end_str


def _metadata_rows(context: GeneratorContext, dataset: ReportDataset) -> list[list[str]]:
    config = _template_config(context)
    header_config = config.get("report_header") if isinstance(config.get("report_header"), dict) else {}
    footer_config = config.get("report_footer") if isinstance(config.get("report_footer"), dict) else {}
    banner_config = header_config.get("brand_banner") if isinstance(header_config.get("brand_banner"), dict) else {}

    org_name = str(getattr(context.organization, "name", None) or "Dhaka Bank").upper()
    rows: list[list[str]] = [
        [org_name, ""],
        ["Data Center Infrastructure Management", ""],
        ["HISTORICAL TELEMETRY REPORT", ""],
        ["Device Monitoring & Operational Reporting", ""],
        ["REPORT INFORMATION", ""],
    ]

    schedule_name = getattr(getattr(context.job, "schedule", None), "name", None)
    definition_code = str(getattr(context.definition, "code", "")).lower()
    is_telem_def = "telemetry" in definition_code
    is_alert_def = "alert" in definition_code

    raw_title = header_config.get("title")
    if raw_title and "telemetry" in raw_title.lower() and not is_telem_def:
        raw_title = None

    template_name = getattr(getattr(context.job, "template", None), "name", None) or (getattr(context, "template_snapshot", None) or {}).get("name")
    title = str(schedule_name or raw_title or template_name or context.definition.code or dataset.title or "Report Export").strip()
    rows.append(["Report Type", "Telemetry Export" if is_telem_def else title])

    raw_subtitle = header_config.get("subtitle")
    if raw_subtitle and ("telemetry" in raw_subtitle.lower() or "ups" in raw_subtitle.lower()) and not is_telem_def:
        raw_subtitle = None

    resolved_subtitle = str(raw_subtitle or dataset.subtitle or "").strip()
    device_label = _device_scope_label(context, dataset)
    if _as_bool(header_config.get("show_device"), True):
        rows.append(["Device Scope", device_label or "All Devices"])

    date_range = _date_range_label(context)
    if date_range and _as_bool(header_config.get("show_date_range"), True):
        rows.append(["Reporting Period", date_range])

    if _as_bool(header_config.get("show_generated_at"), True):
        gen_time = getattr(context, "local_generated_at", None) or getattr(context, "generated_at", None)
        gen_str = gen_time.strftime("%d %b %Y, %H:%M") if gen_time else "--"
        rows.append(["Generated At", gen_str])
    if _as_bool(header_config.get("show_generated_by"), False) or is_telem_def:
        gen_by = getattr(getattr(context.job, "requested_by", None), "full_name", None) or getattr(getattr(context.job, "requested_by", None), "username", None)
        if gen_by:
            rows.append(["Generated By", gen_by])
    if _as_bool(header_config.get("show_timezone"), True) or _as_bool(footer_config.get("show_timezone"), False):
        tz = getattr(context, "timezone", None) or timezone.get_current_timezone()
        tz_name = getattr(tz, "key", None) or getattr(tz, "zone", None) or str(tz)
        offset = (getattr(context, "local_generated_at", None) or timezone.now()).astimezone(tz).utcoffset()
        total_minutes = int(offset.total_seconds() // 60) if offset else 0
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        tz_name = f"{tz_name} (UTC{sign}{hours:02d}:{minutes:02d})"
        if tz_name:
            rows.append(["Timezone", tz_name])

    custom_text = str(footer_config.get("custom_text") or "").strip()
    if custom_text:
        rows.append(["Note", custom_text])
    return rows


def render_csv(dataset: ReportDataset, context: GeneratorContext, output_path: str, filename: str) -> RenderedArtifact:
    table = dataset.primary_table
    if table is None:
        raise ValueError("The dataset does not contain any table to render.")

    config = _template_config(context)
    delimiter = _normalize_delimiter(config.get("csv_delimiter"))
    encoding = _normalize_encoding(config.get("csv_encoding"))
    include_headers = _as_bool(config.get("csv_include_headers"), True)
    include_metadata = _as_bool(config.get("csv_include_metadata"), False)

    with open(output_path, "w", encoding=encoding, errors="replace", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        if include_metadata:
            for row in _metadata_rows(context, dataset):
                writer.writerow(row)
            writer.writerow([])

        if include_headers:
            writer.writerow([_display_column_name(column) for column in table.columns])
        for row in table.rows:
            writer.writerow([format_cell_value(row.get(column)) for column in table.columns])

    return _artifact_metadata(output_path, filename)
