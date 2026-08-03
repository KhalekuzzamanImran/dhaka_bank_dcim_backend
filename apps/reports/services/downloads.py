from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone

from apps.common.audit import write_audit

from ..enums import ReportArtifactFormat
from ..models import ReportArtifact, ReportJob


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


def _content_disposition(filename: str) -> str:
    safe_name = Path(filename or "download").name
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', '_', safe_name).strip("._-") or "download"
    quoted = quote(safe_name)
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'


def _effective_content_type(artifact: ReportArtifact) -> str:
    if artifact.content_type:
        return artifact.content_type
    if artifact.format == ReportArtifactFormat.CSV:
        return "text/csv"
    if artifact.format == ReportArtifactFormat.XLSX:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if artifact.format == ReportArtifactFormat.PDF:
        return "application/pdf"
    guessed = mimetypes.guess_type(artifact.original_filename or "")[0]
    return guessed or "application/octet-stream"


def _parse_range_header(range_header: str | None, size_bytes: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    candidate = range_header.strip()
    if not candidate.startswith("bytes="):
        return None
    candidate = candidate[6:]
    if "," in candidate:
        return None
    start_text, _, end_text = candidate.partition("-")
    if not start_text and not end_text:
        return None
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size_bytes - 1
        else:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(0, size_bytes - suffix_length)
            end = size_bytes - 1
    except (TypeError, ValueError):
        return None
    if size_bytes <= 0:
        return None
    if start < 0 or start >= size_bytes:
        return None
    end = min(end, size_bytes - 1)
    if end < start:
        return None
    return start, end


def _artifact_bytes(artifact: ReportArtifact) -> bytes:
    if not artifact.file:
        raise Http404("The requested artifact is unavailable.")
    if not artifact.file.storage.exists(artifact.file.name):
        raise Http404("The requested artifact is unavailable.")
    with artifact.file.open("rb") as handle:
        return handle.read()


def _build_response(
    *,
    payload: bytes,
    artifact: ReportArtifact,
    request=None,
    status_code: int = 200,
    content_range: str | None = None,
    download_name: str | None = None,
):
    response = HttpResponse(payload, status=status_code, content_type=_effective_content_type(artifact))
    response["Content-Disposition"] = _content_disposition(download_name or artifact.original_filename)
    response["Content-Length"] = str(len(payload))
    response["Accept-Ranges"] = "bytes"
    if content_range:
        response["Content-Range"] = content_range
    _safe_write_audit(
        "REPORT_ARTIFACT_DOWNLOADED",
        "ReportArtifact",
        artifact.pk,
        organization=artifact.job.organization,
        actor=getattr(request, "user", None),
        message="Report artifact downloaded",
        new_value={
            "job_id": str(artifact.job_id),
            "artifact_id": str(artifact.pk),
            "format": artifact.format,
            "filename": artifact.original_filename,
            "size": artifact.size_bytes,
            "checksum": artifact.checksum_sha256,
            "organization_id": str(artifact.job.organization_id) if artifact.job.organization_id else None,
            "data_center_id": str(artifact.job.data_center_id) if artifact.job.data_center_id else None,
            "range": content_range,
        },
    )
    return response


def download_report_artifact(artifact: ReportArtifact, request=None):
    if artifact is None:
        raise Http404("The requested artifact was not found.")
    if not artifact.job_id or not artifact.job:
        raise Http404("The requested artifact is unavailable.")

    if not artifact.file:
        raise Http404("The requested artifact is unavailable.")

    size_bytes = artifact.size_bytes
    if size_bytes <= 0:
        try:
            size_bytes = os.path.getsize(artifact.file.path)
        except OSError:
            raise Http404("The requested artifact is unavailable.") from None

    range_header = getattr(request, "META", {}).get("HTTP_RANGE") if request is not None else None
    range_bounds = _parse_range_header(range_header, size_bytes)
    if range_bounds is None:
        if range_header:
            try:
                artifact.file.open("rb").close()
            except Exception:
                pass
        if not artifact.file.storage.exists(artifact.file.name):
            raise Http404("The requested artifact is unavailable.")
        return _build_response(
            payload=_artifact_bytes(artifact),
            artifact=artifact,
            request=request,
            download_name=artifact.original_filename,
        )

    start, end = range_bounds
    with artifact.file.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(end - start + 1)
    content_range = f"bytes {start}-{end}/{size_bytes}"
    return _build_response(
        payload=payload,
        artifact=artifact,
        request=request,
        status_code=206,
        content_range=content_range,
        download_name=artifact.original_filename,
    )


def download_report_job_artifact(job: ReportJob, request=None):
    if job is None:
        raise Http404("The requested report was not found.")

    artifact = (
        job.artifacts.select_related("job", "job__organization", "job__data_center")
        .order_by("created_at")
        .first()
    )
    if artifact is not None:
        return download_report_artifact(artifact, request=request)

    if not job.file:
        raise Http404("The requested report is unavailable.")

    if not job.file.storage.exists(job.file.name):
        raise Http404("The requested report is unavailable.")

    size_bytes = os.path.getsize(job.file.path)
    content_type = mimetypes.guess_type(job.file.name)[0] or "application/octet-stream"
    response = HttpResponse(job.file.open("rb").read(), content_type=content_type)
    response["Content-Disposition"] = _content_disposition(Path(job.file.name).name)
    response["Content-Length"] = str(size_bytes)
    response["Accept-Ranges"] = "bytes"
    _safe_write_audit(
        "REPORT_LEGACY_FILE_DOWNLOADED",
        "ReportJob",
        job.pk,
        organization=job.organization,
        actor=getattr(request, "user", None),
        message="Legacy report file downloaded",
        new_value={
            "job_id": str(job.pk),
            "filename": Path(job.file.name).name,
            "size": size_bytes,
            "organization_id": str(job.organization_id) if job.organization_id else None,
            "data_center_id": str(job.data_center_id) if job.data_center_id else None,
        },
    )
    return response
