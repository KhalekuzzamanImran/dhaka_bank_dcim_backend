from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from apps.common.audit import write_audit

from ..constants import normalize_report_format
from ..enums import ReportTriggerSource
from ..models import ReportJob, ReportJobStatus
from .definitions import get_active_definition_by_code, get_definition_by_code, get_definition_code_for_report_type
from ..generators import GeneratorContext, RenderedArtifact, get_generator_class


@dataclass(frozen=True)
class ReportGenerationResult:
    job: ReportJob
    artifacts: list[RenderedArtifact]
    duplicate: bool = False


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


def _format_validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if getattr(exc, "message_dict", None):
            messages = []
            for values in exc.message_dict.values():
                messages.extend(str(value) for value in values if str(value))
            if messages:
                return "; ".join(messages)
        if getattr(exc, "messages", None):
            messages = [str(message) for message in exc.messages if str(message)]
            if messages:
                return "; ".join(messages)
    return str(exc)


def _resolve_definition_for_job(job: ReportJob):
    if job.definition_id and job.definition:
        return job.definition
    if job.template_id and job.template and job.template.definition_id:
        return job.template.definition
    template_snapshot = job.template_snapshot if isinstance(job.template_snapshot, dict) else {}
    definition_code = template_snapshot.get("definition_code")
    if definition_code:
        definition = get_definition_by_code(definition_code)
        if definition:
            return definition
    parameters = job.parameters_snapshot if isinstance(job.parameters_snapshot, dict) else job.parameters or {}
    report_type = parameters.get("report_type") or job.report_type
    definition_code = get_definition_code_for_report_type(report_type)
    if definition_code:
        return get_definition_by_code(definition_code) or get_active_definition_by_code(definition_code)
    return None


def _normalize_requested_formats(primary_format, attachment_formats):
    formats: list[str] = []

    def _append(value):
        normalized = normalize_report_format(value)
        if not normalized:
            return
        if normalized == "PDF_CSV":
            for candidate in ("PDF", "CSV"):
                if candidate not in formats:
                    formats.append(candidate)
            return
        if normalized not in formats:
            formats.append(normalized)

    _append(primary_format)
    for value in attachment_formats or []:
        _append(value)

    if not formats:
        formats.append("CSV")
    return formats


def _get_output_config(job: ReportJob) -> dict:
    return job.output_config_snapshot if isinstance(job.output_config_snapshot, dict) else {}


def _build_context(job: ReportJob, definition) -> GeneratorContext:
    parameters = deepcopy(job.parameters_snapshot if isinstance(job.parameters_snapshot, dict) and job.parameters_snapshot else job.parameters or {})
    template_snapshot = deepcopy(job.template_snapshot if isinstance(job.template_snapshot, dict) else {})
    scope_snapshot = deepcopy(job.scope_snapshot if isinstance(job.scope_snapshot, dict) else {})
    output_config_snapshot = deepcopy(_get_output_config(job))
    return GeneratorContext(
        job=job,
        definition=definition,
        organization=job.organization,
        data_center=job.data_center,
        parameters=parameters,
        template_snapshot=template_snapshot,
        scope_snapshot=scope_snapshot,
        output_config_snapshot=output_config_snapshot,
        timezone=timezone.get_current_timezone(),
        generated_at=timezone.now(),
    )


def _claim_job(report_job_id):
    with transaction.atomic():
        job = (
            ReportJob.objects.select_for_update()
            .filter(pk=report_job_id)
            .first()
        )
        if not job:
            raise ValueError(f"Report job {report_job_id} does not exist.")
        if job.status == ReportJobStatus.CANCELLED:
            job._generation_claimed = False
            return job
        if job.status == ReportJobStatus.COMPLETED and job.file:
            job._generation_claimed = False
            return job
        if job.status in {ReportJobStatus.RUNNING, ReportJobStatus.PROCESSING, ReportJobStatus.QUEUED}:
            job._generation_claimed = False
            return job
        job.status = ReportJobStatus.RUNNING
        job.started_at = job.started_at or timezone.now()
        job.progress_percent = max(job.progress_percent or 0, 5)
        job.progress_message = "Report generation started."
        job.error_message = ""
        job.failed_at = None
        job.cancelled_at = None
        job.save(update_fields=["status", "started_at", "progress_percent", "progress_message", "error_message", "failed_at", "cancelled_at", "updated_at"])
        job._generation_claimed = True
        _safe_write_audit(
            "REPORT_GENERATION_STARTED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=job.requested_by,
            message=f"Report generation started for {job.report_type or 'unknown'}",
        )
        return job


def _render_requested_artifacts(job: ReportJob, definition, generator, context: GeneratorContext):
    requested_formats = _normalize_requested_formats(
        context.output_config_snapshot.get("primary_format") or context.output_config_snapshot.get("output_format"),
        context.output_config_snapshot.get("attachment_formats"),
    )
    generator_supported = {str(value).strip().upper() for value in getattr(generator, "supported_formats", ()) if str(value).strip()}
    definition_supported = {str(value).strip().upper() for value in getattr(definition, "supported_formats", ()) if str(value).strip()}

    artifacts: list[RenderedArtifact] = []
    dataset = generator.build_dataset(context)
    dataset.tables = [
        type(table)(
            name=table.name,
            columns=list(table.columns),
            rows=[dict(row) for row in table.rows],
            title=table.title,
            description=table.description,
            primary=table.primary,
            include_in_csv=table.include_in_csv,
        )
        for table in dataset.tables
    ]
    for output_format in requested_formats:
        if generator_supported and output_format not in generator_supported:
            raise ValidationError({"output_format": f"Generator does not support {output_format} output."})
        if definition_supported and output_format not in definition_supported:
            raise ValidationError({"output_format": f"Report definition does not support {output_format} output."})
        temp_handle = tempfile.NamedTemporaryFile(delete=False, suffix=f".{output_format.lower()}")
        temp_handle.close()
        try:
            artifact = generator.render(context, dataset, output_format, temp_handle.name)
            artifacts.append(artifact)
        except Exception:
            try:
                os.unlink(temp_handle.name)
            except OSError:
                pass
            raise
    return artifacts


def _attach_primary_artifact(job: ReportJob, artifacts: list[RenderedArtifact]):
    if not artifacts:
        raise ValueError("No generated artifact was produced.")

    primary = artifacts[0]
    if job.file:
        try:
            job.file.delete(save=False)
        except Exception:
            pass

    with open(primary.path, "rb") as handle:
        job.file.save(primary.filename, File(handle), save=False)
    return primary


def _cleanup_artifacts(artifacts: list[RenderedArtifact]):
    for artifact in artifacts:
        try:
            os.unlink(artifact.path)
        except OSError:
            pass


def _finalize_success(job: ReportJob, artifacts: list[RenderedArtifact]):
    primary = _attach_primary_artifact(job, artifacts)
    job.status = ReportJobStatus.COMPLETED
    job.completed_at = timezone.now()
    job.failed_at = None
    job.progress_percent = 100
    job.progress_message = "Report generation completed."
    job.error_message = ""
    job.save(update_fields=["file", "status", "completed_at", "failed_at", "progress_percent", "progress_message", "error_message", "updated_at"])
    _safe_write_audit(
        "REPORT_GENERATED",
        "ReportJob",
        job.pk,
        organization=job.organization,
        actor=job.requested_by,
        message=f"Report generated successfully for {getattr(job.definition, 'code', job.report_type or 'unknown')}",
    )
    job._rendered_artifacts = artifacts  # transient compatibility hook
    job._primary_rendered_artifact = primary
    return job


def _finalize_failure(job: ReportJob, exc: Exception):
    message = _format_validation_message(exc)
    with transaction.atomic():
        locked = ReportJob.objects.select_for_update().filter(pk=job.pk).first()
        if not locked:
            return job
        if locked.file:
            try:
                locked.file.delete(save=False)
            except Exception:
                pass
            locked.file = None
        locked.status = ReportJobStatus.FAILED
        locked.completed_at = timezone.now()
        locked.failed_at = timezone.now()
        locked.progress_percent = min(max(locked.progress_percent or 0, 0), 99)
        locked.progress_message = "Report generation failed."
        locked.error_message = message
        locked.save(update_fields=["file", "status", "completed_at", "failed_at", "progress_percent", "progress_message", "error_message", "updated_at"])
        _safe_write_audit(
            "REPORT_GENERATION_FAILED",
            "ReportJob",
            locked.pk,
            organization=locked.organization,
            actor=locked.requested_by,
            message=message,
        )
        return locked


def generate_report_job(report_job_id):
    job = _claim_job(report_job_id)
    if not getattr(job, "_generation_claimed", False):
        return job
    if job.status in {ReportJobStatus.CANCELLED, ReportJobStatus.COMPLETED} and job.file:
        return job
    if job.status in {ReportJobStatus.RUNNING, ReportJobStatus.PROCESSING, ReportJobStatus.QUEUED} and job.file:
        return job

    definition = _resolve_definition_for_job(job)
    if not definition:
        return _finalize_failure(job, ValidationError({"definition": "Unable to resolve a report definition for this job."}))

    try:
        generator_class = get_generator_class(definition.generator_key)
        generator = generator_class()
    except Exception as exc:
        return _finalize_failure(job, exc)

    context = _build_context(job, definition)

    artifacts: list[RenderedArtifact] = []
    try:
        generator.validate_parameters(context)
        artifacts = _render_requested_artifacts(job, definition, generator, context)
        with transaction.atomic():
            locked = ReportJob.objects.select_for_update().get(pk=job.pk)
            if locked.status == ReportJobStatus.CANCELLED:
                return locked
            final_job = _finalize_success(locked, artifacts)
            _cleanup_artifacts(artifacts)
            return final_job
    except Exception as exc:
        locked = _finalize_failure(job, exc)
        _cleanup_artifacts(artifacts)
        return locked
