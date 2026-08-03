from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.common.audit import write_audit

from ..enums import ReportArtifactFormat
from ..models import ReportArtifact, ReportJob


MIME_TYPES = {
    ReportArtifactFormat.CSV: "text/csv",
    ReportArtifactFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ReportArtifactFormat.PDF: "application/pdf",
}


@dataclass(frozen=True)
class ReportArtifactPersistenceResult:
    artifact: ReportArtifact
    created: bool


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


def _normalize_format(value: object) -> str:
    candidate = str(value or "").strip().upper()
    if candidate not in ReportArtifactFormat.values:
        raise ValidationError({"format": "Unsupported report artifact format."})
    return candidate


def _allowed_output_formats(job: ReportJob) -> set[str]:
    output_config = job.output_config_snapshot if isinstance(job.output_config_snapshot, dict) else {}
    template_snapshot = job.template_snapshot if isinstance(job.template_snapshot, dict) else {}
    candidates = []
    for source in (
        output_config.get("primary_format"),
        output_config.get("output_format"),
        *(output_config.get("attachment_formats") or []),
        template_snapshot.get("primary_format"),
        template_snapshot.get("output_format"),
        *(template_snapshot.get("attachment_formats") or []),
        getattr(job.template, "primary_format", None),
        getattr(job.template, "attachment_formats", None) or [],
    ):
        if isinstance(source, (list, tuple, set)):
            candidates.extend(source)
        else:
            candidates.append(source)

    normalized: set[str] = set()
    for value in candidates:
        candidate = str(value or "").strip().upper()
        if not candidate:
            continue
        if candidate == "PDF_CSV":
            normalized.update({"PDF", "CSV"})
            continue
        normalized.add(candidate)
    return normalized


def _safe_filename_base(job: ReportJob) -> str:
    template_name = None
    if isinstance(job.template_snapshot, dict):
        template_name = job.template_snapshot.get("name")
    if not template_name and getattr(job.template, "name", None):
        template_name = job.template.name
    if not template_name and getattr(job.definition, "name", None):
        template_name = job.definition.name
    if not template_name:
        template_name = job.report_type or "report"
    base = slugify(str(template_name)) or "report"
    return re.sub(r"[^a-z0-9_-]+", "-", base).strip("-_") or "report"


def _deterministic_storage_name(job: ReportJob, filename: str) -> str:
    created_at = getattr(job, "created_at", None) or getattr(job, "queued_at", None) or timezone.now()
    date_part = timezone.localdate(created_at).strftime("%Y-%m-%d")
    short_job_id = str(job.pk).replace("-", "")[:8]
    extension = Path(filename).suffix.lower().lstrip(".")
    safe_filename = f"{_safe_filename_base(job)}_{date_part}_job-{short_job_id}.{extension}"
    return f"reports/{job.organization_id}/{created_at:%Y/%m}/{job.pk}/{safe_filename}"


def _checksum_and_size(path: str) -> tuple[int, str]:
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return size, digest.hexdigest()


def _cleanup_file(path: str | None):
    if not path:
        return
    try:
        default_storage.delete(path)
    except Exception:
        pass


def _validate_source_file(generated_file, *, format: str, filename: str, content_type: str):
    source_path = getattr(generated_file, "path", None)
    if not source_path or not os.path.exists(source_path):
        raise ValidationError({"generated_file": "Generated file does not exist."})
    if Path(filename).suffix.lower().lstrip(".") != format.lower():
        raise ValidationError({"filename": "Filename extension does not match the requested format."})
    expected_mime = MIME_TYPES.get(format)
    if expected_mime and content_type and content_type != expected_mime:
        raise ValidationError({"content_type": f"Content type must be {expected_mime} for {format} artifacts."})
    return source_path


def create_report_artifact(*, job, generated_file, format, filename, content_type):
    if job is None:
        raise ValidationError({"job": "Job is required."})

    normalized_format = _normalize_format(format)
    source_path = _validate_source_file(generated_file, format=normalized_format, filename=filename, content_type=content_type)
    normalized_content_type = content_type or MIME_TYPES.get(normalized_format, "")
    if normalized_content_type not in {"", MIME_TYPES.get(normalized_format, "")}:
        raise ValidationError({"content_type": f"Unsupported content type for {normalized_format} artifacts."})

    source_size, source_checksum = _checksum_and_size(source_path)
    allowed_formats = _allowed_output_formats(job)
    if allowed_formats and normalized_format not in allowed_formats:
        raise ValidationError({"format": f"Output format {normalized_format} is not allowed for this job."})

    retention_days = getattr(settings, "REPORT_ARTIFACT_RETENTION_DAYS", 90)
    retention_expires_at = timezone.now() + timedelta(days=int(retention_days))
    storage_name = _deterministic_storage_name(job, filename)
    original_filename = Path(filename).name

    locked_job = None
    artifact = None
    try:
        with transaction.atomic():
            locked_job = ReportJob.objects.select_for_update().get(pk=job.pk)
            existing = (
                ReportArtifact.objects.select_for_update()
                .filter(job=locked_job, format=normalized_format)
                .first()
            )

            if existing:
                existing_file_name = existing.file.name if existing.file else ""
                if (
                    existing_file_name
                    and default_storage.exists(existing_file_name)
                    and existing.size_bytes == source_size
                    and existing.checksum_sha256.lower() == source_checksum.lower()
                    and existing.content_type == normalized_content_type
                ):
                    return ReportArtifactPersistenceResult(artifact=existing, created=False)

                if existing_file_name and default_storage.exists(existing_file_name):
                    _cleanup_file(existing_file_name)

            if default_storage.exists(storage_name):
                _cleanup_file(storage_name)

            try:
                with open(source_path, "rb") as source_handle:
                    saved_name = default_storage.save(storage_name, File(source_handle))

                artifact = existing or ReportArtifact(job=locked_job, format=normalized_format)
                artifact.file.name = saved_name
                artifact.original_filename = original_filename
                artifact.content_type = normalized_content_type
                artifact.size_bytes = source_size
                artifact.checksum_sha256 = source_checksum
                artifact.retention_expires_at = retention_expires_at
                artifact.save()
            except Exception:
                _cleanup_file(storage_name)
                raise

        _safe_write_audit(
            "REPORT_ARTIFACT_CREATED",
            "ReportArtifact",
            artifact.pk,
            organization=locked_job.organization,
            actor=locked_job.requested_by,
            message="Report artifact created",
            new_value={
                "job_id": str(locked_job.pk),
                "artifact_id": str(artifact.pk),
                "format": normalized_format,
                "filename": original_filename,
                "size": source_size,
                "checksum": source_checksum,
                "organization_id": str(locked_job.organization_id),
                "data_center_id": str(locked_job.data_center_id) if locked_job.data_center_id else None,
            },
        )
        return ReportArtifactPersistenceResult(artifact=artifact, created=True)
    except Exception as exc:
        _safe_write_audit(
            "REPORT_ARTIFACT_PERSISTENCE_FAILED",
            "ReportArtifact",
            getattr(artifact, "pk", None) or getattr(job, "pk", None),
            organization=getattr(locked_job, "organization", None) or getattr(job, "organization", None),
            actor=getattr(locked_job, "requested_by", None) or getattr(job, "requested_by", None),
            message=str(exc),
            new_value={
                "job_id": str(getattr(locked_job, "pk", None) or getattr(job, "pk", None)),
                "format": normalized_format,
                "filename": original_filename,
                "organization_id": str(getattr(locked_job, "organization_id", None) or getattr(job, "organization_id", None) or ""),
                "data_center_id": str(getattr(locked_job, "data_center_id", None) or getattr(job, "data_center_id", None) or ""),
            },
        )
        raise
