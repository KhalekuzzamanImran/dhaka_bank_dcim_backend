from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.audit import write_audit
from .observability import log_report_event, log_report_metric

from ..models import ReportArtifact


logger = logging.getLogger(__name__)


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


@dataclass
class RetentionCleanupResult:
    examined: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[dict[str, object]] = field(default_factory=list)
    disabled: bool = False
    dry_run: bool = False
    batch_size: int = 0

    def as_dict(self):
        return asdict(self)


def _normalize_batch_size(batch_size: int | None) -> int:
    value = int(batch_size or getattr(settings, "REPORT_ARTIFACT_CLEANUP_BATCH_SIZE", 200))
    if value <= 0:
        raise ValueError("Batch size must be greater than zero.")
    return value


def _same_legacy_storage_name(job, storage_name: str) -> bool:
    if not job or not storage_name or not getattr(job, "file", None):
        return False
    try:
        return job.file.name == storage_name
    except Exception:
        return False


def _artifact_audit_metadata(artifact: ReportArtifact, *, storage_name: str, error_summary: str | None = None):
    payload = {
        "artifact_id": str(artifact.pk),
        "job_id": str(artifact.job_id),
        "format": artifact.format,
        "storage_name": storage_name,
        "retention_expires_at": artifact.retention_expires_at.isoformat() if artifact.retention_expires_at else None,
        "organization_id": str(artifact.job.organization_id) if artifact.job.organization_id else None,
        "data_center_id": str(artifact.job.data_center_id) if artifact.job.data_center_id else None,
    }
    if error_summary:
        payload["error_summary"] = error_summary
    return payload


def _delete_locked_artifact(artifact: ReportArtifact, *, dry_run: bool) -> tuple[str, dict[str, object] | None]:
    storage_name = artifact.file.name if artifact.file else ""
    if dry_run:
        return "skipped", None

    if not storage_name:
        if _same_legacy_storage_name(artifact.job, storage_name):
            artifact.job.file = None
            artifact.job.save(update_fields=["file", "updated_at"])
        artifact.delete()
        return "missing", _artifact_audit_metadata(artifact, storage_name=storage_name)

    storage = artifact.file.storage
    if not storage.exists(storage_name):
        if _same_legacy_storage_name(artifact.job, storage_name):
            artifact.job.file = None
            artifact.job.save(update_fields=["file", "updated_at"])
        artifact.delete()
        return "missing", _artifact_audit_metadata(artifact, storage_name=storage_name)

    if _same_legacy_storage_name(artifact.job, storage_name):
        artifact.job.file = None
        artifact.job.save(update_fields=["file", "updated_at"])

    storage.delete(storage_name)
    if storage.exists(storage_name):
        raise OSError(f"Artifact file still exists after deletion: {storage_name}")
    artifact.delete()
    return "deleted", _artifact_audit_metadata(artifact, storage_name=storage_name)


def cleanup_expired_report_artifacts(*, now=None, batch_size=None, dry_run=False):
    effective_now = now or timezone.now()
    effective_batch_size = _normalize_batch_size(batch_size)
    result = RetentionCleanupResult(dry_run=dry_run, batch_size=effective_batch_size)

    if not getattr(settings, "REPORT_ARTIFACT_CLEANUP_ENABLED", True):
        result.disabled = True
        log_report_event(logger, "Report artifact cleanup disabled", execution_time_ms=0)
        return result.as_dict()

    with transaction.atomic():
        expired_artifacts = list(
            ReportArtifact.objects.select_for_update(skip_locked=True)
            .select_related("job", "job__organization")
            .filter(retention_expires_at__isnull=False, retention_expires_at__lte=effective_now)
            .order_by("retention_expires_at", "created_at", "pk")[:effective_batch_size]
        )
        result.examined = len(expired_artifacts)

        for artifact in expired_artifacts:
            if dry_run:
                result.skipped += 1
                continue
            try:
                with transaction.atomic():
                    locked = ReportArtifact.objects.select_for_update().select_related("job", "job__organization").get(pk=artifact.pk)
                    action, metadata = _delete_locked_artifact(locked, dry_run=False)
                    if action in {"deleted", "missing"}:
                        result.deleted += 1
                        _safe_write_audit(
                            "REPORT_ARTIFACT_EXPIRED_DELETED" if action == "deleted" else "REPORT_ARTIFACT_FILE_MISSING",
                            "ReportArtifact",
                            locked.pk,
                            organization=locked.job.organization,
                            actor=None,
                            message="Expired report artifact deleted" if action == "deleted" else "Report artifact file was already missing",
                            new_value=metadata or {},
                        )
            except Exception as exc:
                result.failed += 1
                error_summary = str(exc)
                result.errors.append(
                    {
                        "artifact_id": str(artifact.pk),
                        "job_id": str(artifact.job_id),
                        "format": artifact.format,
                        "storage_name": artifact.file.name if artifact.file else "",
                        "error_summary": error_summary,
                    }
                )
                logger.warning(
                    "Report artifact cleanup failed artifact=%s job=%s format=%s error=%s",
                    artifact.pk,
                    artifact.job_id,
                    artifact.format,
                    error_summary,
                )
                _safe_write_audit(
                    "REPORT_ARTIFACT_DELETION_FAILED",
                    "ReportArtifact",
                    artifact.pk,
                    organization=artifact.job.organization,
                    actor=None,
                    message=error_summary,
                    new_value=_artifact_audit_metadata(artifact, storage_name=artifact.file.name if artifact.file else "", error_summary=error_summary),
                )
                continue

    _safe_write_audit(
        "REPORT_ARTIFACT_CLEANUP_BATCH_COMPLETED",
        "ReportArtifact",
        None,
        organization=None,
        actor=None,
        message="Report artifact cleanup batch completed",
        new_value=result.as_dict(),
    )
    log_report_event(
        logger,
        "Report artifact cleanup batch completed",
        execution_time_ms=0,
        artifact_count=result.examined,
        retry_count=result.failed,
    )
    log_report_metric(logger, "report_artifact_cleanup_examined", value=result.examined)
    log_report_metric(logger, "report_artifact_cleanup_deleted", value=result.deleted)
    return result.as_dict()
