from __future__ import annotations

import hashlib
import os
from pathlib import Path

from django.core.files import File
from django.db import transaction

from apps.reports.models import ReportArtifact, ReportArtifactStatus, ReportArtifactType, ReportJob


def _safe_name(component: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(component or "").strip()).strip("_") or "report"


class ReportArtifactService:
    @classmethod
    def create_primary_artifact(cls, *, job: ReportJob, rendered_artifact):
        path = Path(rendered_artifact.file_path)
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        extension = path.suffix.lstrip(".") or "csv"
        file_name = f"{_safe_name(job.report_type or job.definition_code_snapshot or 'report')}_{_safe_name(getattr(job.organization, 'code', job.organization_id))}_{job.pk}.{extension}"

        with transaction.atomic():
            artifact = ReportArtifact(job=job, artifact_type=ReportArtifactType.PRIMARY, format=rendered_artifact.format, file_name=file_name, content_type=rendered_artifact.content_type, size_bytes=size, checksum_sha256=sha256, status=ReportArtifactStatus.GENERATING)
            with open(path, "rb") as handle:
                artifact.file.save(file_name, File(handle), save=False)
            artifact.status = ReportArtifactStatus.AVAILABLE
            artifact.save()
            job.file = artifact.file
            job.save(update_fields=["file", "updated_at"])
        try:
            os.remove(path)
        except OSError:
            pass
        return artifact
