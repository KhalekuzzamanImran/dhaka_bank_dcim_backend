from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from django.db import migrations
from django.utils import timezone

from apps.reports.domain import (
    REPORT_DEFINITION_SEEDS,
    ReportArtifactStatus,
    ReportArtifactType,
    ReportDeliveryStatus,
    ReportRecipientChannel,
    ReportRecipientType,
    ReportScheduleStatus,
    canonical_report_definition_code,
)


logger = logging.getLogger(__name__)


def _seed_definitions(apps):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    for seed in REPORT_DEFINITION_SEEDS:
        defaults = {key: value for key, value in seed.items() if key != "code"}
        ReportDefinition.objects.update_or_create(code=seed["code"], defaults=defaults)


def _build_definition_map(apps):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    return {definition.code: definition for definition in ReportDefinition.objects.all()}


def _file_metadata(file_field):
    if not file_field:
        return None

    file_name = Path(file_field.name).name
    content_type = "application/octet-stream"
    if file_name.lower().endswith(".csv"):
        content_type = "text/csv"
    elif file_name.lower().endswith(".pdf"):
        content_type = "application/pdf"
    elif file_name.lower().endswith(".xlsx"):
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    size_bytes = None
    checksum = None
    try:
        size_bytes = int(file_field.size)
    except Exception:
        size_bytes = None

    try:
        digest = hashlib.sha256()
        file_field.open("rb")
        for chunk in file_field.chunks():
            digest.update(chunk)
        checksum = digest.hexdigest()
    except Exception:
        checksum = None

    return {
        "file_name": file_name,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "checksum_sha256": checksum,
    }


def _select_template(schedule, templates_by_definition):
    candidates = templates_by_definition.get(schedule.definition_id or schedule.report_type, [])
    if not candidates:
        return None

    if schedule.organization_id:
        org_candidates = [template for template in candidates if template.organization_id == schedule.organization_id]
        if len(org_candidates) == 1:
            return org_candidates[0]
        if len(org_candidates) > 1:
            return None

    global_candidates = [template for template in candidates if template.organization_id is None]
    if len(global_candidates) == 1:
        return global_candidates[0]
    return None


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias

    ReportTemplate = apps.get_model("reports", "ReportTemplate")
    ReportSchedule = apps.get_model("reports", "ReportSchedule")
    ReportScheduleRecipient = apps.get_model("reports", "ReportScheduleRecipient")
    ReportJob = apps.get_model("reports", "ReportJob")
    ReportArtifact = apps.get_model("reports", "ReportArtifact")
    ReportDelivery = apps.get_model("reports", "ReportDelivery")

    _seed_definitions(apps)
    definitions = _build_definition_map(apps)

    templates_by_definition = {}
    for template in ReportTemplate.objects.using(db).all().iterator():
        config = template.config if isinstance(template.config, dict) else {}
        report_type = canonical_report_definition_code(config.get("report_type")) or canonical_report_definition_code(template.code)
        definition = definitions.get(report_type) if report_type else None
        if definition and template.definition_id != definition.id:
            ReportTemplate.objects.using(db).filter(pk=template.pk).update(definition_id=definition.id)
        if definition:
            templates_by_definition.setdefault(definition.id, []).append(template)

    for schedule in ReportSchedule.objects.using(db).select_related("definition", "template", "organization", "data_center", "last_job").all().iterator():
        legacy_report_type = canonical_report_definition_code(schedule.report_type)
        if not legacy_report_type and isinstance(schedule.parameters, dict):
            legacy_report_type = canonical_report_definition_code(schedule.parameters.get("report_type"))
        if not legacy_report_type and schedule.template_id and schedule.template.definition_id:
            legacy_report_type = schedule.template.definition.code

        definition = definitions.get(legacy_report_type) if legacy_report_type else None
        updates = {}
        if definition and schedule.definition_id != definition.id:
            updates["definition_id"] = definition.id
            updates["report_type"] = definition.code

        if schedule.template_id is None and definition is not None:
            candidates = [
                template
                for template in templates_by_definition.get(definition.id, [])
                if template.organization_id in {None, schedule.organization_id}
            ]
            if schedule.organization_id:
                org_candidates = [template for template in candidates if template.organization_id == schedule.organization_id]
                if len(org_candidates) == 1:
                    updates["template_id"] = org_candidates[0].id
                elif len(org_candidates) == 0:
                    global_candidates = [template for template in candidates if template.organization_id is None]
                    if len(global_candidates) == 1:
                        updates["template_id"] = global_candidates[0].id
                else:
                    logger.warning(
                        "Ambiguous report template mapping for schedule=%s organization=%s definition=%s",
                        schedule.pk,
                        schedule.organization_id,
                        definition.code,
                    )
            elif len(candidates) == 1:
                updates["template_id"] = candidates[0].id

        if schedule.status is None:
            updates["status"] = ReportScheduleStatus.ACTIVE if schedule.is_active else ReportScheduleStatus.PAUSED
        if schedule.primary_format is None:
            if schedule.output_format == "PDF_CSV":
                updates["primary_format"] = "PDF"
            elif schedule.output_format in {"PDF", "CSV"}:
                updates["primary_format"] = schedule.output_format
        if not schedule.attachment_formats:
            attachment_formats = []
            if schedule.attach_raw_data:
                attachment_formats.append("CSV")
            if attachment_formats:
                updates["attachment_formats"] = attachment_formats
        if not schedule.recurrence_rule:
            updates["recurrence_rule"] = {
                "frequency": schedule.frequency,
                "delivery_time": schedule.delivery_time.strftime("%H:%M:%S") if schedule.delivery_time else None,
            }
        if schedule.start_at is None and schedule.last_run_at:
            updates["start_at"] = schedule.last_run_at
        if schedule.last_success_at is None and schedule.last_run_at and schedule.last_delivery_status in {"COMPLETED", "DELIVERED", "SUCCESS"}:
            updates["last_success_at"] = schedule.last_run_at
        if schedule.consecutive_failure_count is None:
            updates["consecutive_failure_count"] = 0

        if updates:
            ReportSchedule.objects.using(db).filter(pk=schedule.pk).update(**updates)

        recipients_exist = ReportScheduleRecipient.objects.using(db).filter(schedule_id=schedule.pk).exists()
        if not recipients_exist:
            recipient_rows = []
            recipients = schedule.recipients if isinstance(schedule.recipients, list) else []
            for destination in recipients:
                recipient_rows.append(
                    ReportScheduleRecipient(
                        schedule_id=schedule.pk,
                        channel=ReportRecipientChannel.EMAIL,
                        recipient_type=ReportRecipientType.TO,
                        destination=str(destination).strip().lower(),
                        display_name=str(destination).strip(),
                        is_active=True,
                    )
                )
            sms_recipients = schedule.sms_recipients if isinstance(schedule.sms_recipients, list) else []
            for destination in sms_recipients:
                recipient_rows.append(
                    ReportScheduleRecipient(
                        schedule_id=schedule.pk,
                        channel=ReportRecipientChannel.SMS,
                        recipient_type=ReportRecipientType.TO,
                        destination=str(destination).strip(),
                        display_name=str(destination).strip(),
                        is_active=True,
                    )
                )
            if recipient_rows:
                ReportScheduleRecipient.objects.using(db).bulk_create(recipient_rows, ignore_conflicts=True)

        job = schedule.last_job if schedule.last_job_id else None
        if job and not job.definition_id:
            job_definition_code = None
            if job.template_id and job.template.definition_id:
                job_definition_code = job.template.definition.code
            elif isinstance(job.parameters, dict):
                job_definition_code = canonical_report_definition_code(job.parameters.get("report_type"))
            elif schedule.definition_id:
                job_definition_code = schedule.definition.code
            definition = definitions.get(job_definition_code) if job_definition_code else None
            if definition:
                ReportJob.objects.using(db).filter(pk=job.pk).update(definition_id=definition.id)

    for job in ReportJob.objects.using(db).select_related("template", "schedule", "schedule__definition").all().iterator():
        updates = {}
        if not job.definition_id:
            job_definition_code = None
            if job.template_id and job.template.definition_id:
                job_definition_code = job.template.definition.code
            elif isinstance(job.parameters, dict):
                job_definition_code = canonical_report_definition_code(job.parameters.get("report_type"))
            elif job.schedule_id and job.schedule.definition_id:
                job_definition_code = job.schedule.definition.code
            definition = definitions.get(job_definition_code) if job_definition_code else None
            if definition:
                updates["definition_id"] = definition.id

        if job.schedule_id and not job.scheduled_for and job.schedule.next_run_at:
            updates["scheduled_for"] = job.schedule.next_run_at

        if not job.definition_code_snapshot and job.definition_id:
            updates["definition_code_snapshot"] = job.definition.code
        if not job.definition_version_snapshot and job.definition_id:
            updates["definition_version_snapshot"] = job.definition.version
        if not job.template_name_snapshot and job.template_id:
            updates["template_name_snapshot"] = job.template.name
        if not job.template_version_snapshot and job.template_id:
            updates["template_version_snapshot"] = job.template.version
        if not job.template_config_snapshot and job.template_id:
            updates["template_config_snapshot"] = job.template.config if isinstance(job.template.config, dict) else {}
        if not job.parameters_snapshot:
            updates["parameters_snapshot"] = job.parameters if isinstance(job.parameters, dict) else {}
        if not job.scope_snapshot:
            updates["scope_snapshot"] = {
                "organization_id": str(job.organization_id) if job.organization_id else None,
                "data_center_id": str(job.data_center_id) if job.data_center_id else None,
                "schedule_id": str(job.schedule_id) if job.schedule_id else None,
                "template_id": str(job.template_id) if job.template_id else None,
                "definition_code": job.definition.code if job.definition_id else None,
            }
        if not job.recipient_snapshot:
            recipients = []
            if job.schedule_id:
                recipients = [
                    {
                        "channel": entry.channel,
                        "recipient_type": entry.recipient_type,
                        "destination": entry.destination,
                        "display_name": entry.display_name,
                        "user_id": str(entry.user_id) if entry.user_id else None,
                    }
                    for entry in job.schedule.recipient_entries.filter(is_active=True)
                ]
            updates["recipient_snapshot"] = recipients or {
                "email_recipients": list(job.parameters.get("recipients", [])) if isinstance(job.parameters, dict) else [],
                "sms_recipients": list(job.parameters.get("sms_recipients", [])) if isinstance(job.parameters, dict) else [],
            }
        if not job.output_config_snapshot:
            updates["output_config_snapshot"] = {
                "primary_format": getattr(job.schedule, "primary_format", None) if job.schedule_id else None,
                "attachment_formats": list(getattr(job.schedule, "attachment_formats", [])) if job.schedule_id and isinstance(job.schedule.attachment_formats, list) else [],
            }

        if updates:
            ReportJob.objects.using(db).filter(pk=job.pk).update(**updates)

        if getattr(job, "file", None):
            if not ReportArtifact.objects.using(db).filter(job_id=job.pk, artifact_type=ReportArtifactType.PRIMARY).exists():
                metadata = _file_metadata(job.file)
                artifact_format = None
                if isinstance(job.parameters, dict):
                    artifact_format = job.parameters.get("output_format") or job.parameters.get("primary_format")
                if not artifact_format and metadata and metadata["file_name"].lower().endswith(".pdf"):
                    artifact_format = "PDF"
                elif not artifact_format and metadata and metadata["file_name"].lower().endswith(".xlsx"):
                    artifact_format = "XLSX"
                else:
                    artifact_format = artifact_format or "CSV"
                artifact_defaults = {
                    "artifact_type": ReportArtifactType.PRIMARY,
                    "format": artifact_format,
                    "status": ReportArtifactStatus.AVAILABLE,
                    "expires_at": None,
                }
                if metadata:
                    artifact_defaults.update(metadata)
                artifact = ReportArtifact.objects.using(db).create(job_id=job.pk, **artifact_defaults)
                if job.file.name:
                    ReportArtifact.objects.using(db).filter(pk=artifact.pk).update(file=job.file.name)

    for schedule in ReportSchedule.objects.using(db).select_related("last_job").all().iterator():
        job = schedule.last_job if schedule.last_job_id else None
        if not job:
            continue
        recipients = list(schedule.recipient_entries.filter(is_active=True))
        if not recipients:
            continue
        if ReportDelivery.objects.using(db).filter(schedule_id=schedule.pk).exists():
            continue

        status_map = {
            "COMPLETED": ReportDeliveryStatus.DELIVERED,
            "DELIVERED": ReportDeliveryStatus.DELIVERED,
            "SUCCESS": ReportDeliveryStatus.DELIVERED,
            "FAILED": ReportDeliveryStatus.FAILED,
            "ERROR": ReportDeliveryStatus.FAILED,
            "PENDING": ReportDeliveryStatus.PENDING,
            "QUEUED": ReportDeliveryStatus.PENDING,
            "PROCESSING": ReportDeliveryStatus.SENDING,
            "RUNNING": ReportDeliveryStatus.SENDING,
            "ACCEPTED": ReportDeliveryStatus.ACCEPTED,
            "SKIPPED": ReportDeliveryStatus.SKIPPED,
        }
        delivery_status = status_map.get(schedule.last_delivery_status or job.status, ReportDeliveryStatus.PENDING)
        delivered_at = schedule.last_sent_at or job.completed_at or job.started_at
        attempted_at = delivered_at or schedule.last_run_at or job.started_at or timezone.now()
        queued_at = schedule.last_run_at or job.queued_at or attempted_at

        deliveries = []
        for entry in recipients:
            deliveries.append(
                ReportDelivery(
                    job_id=job.pk,
                    schedule_id=schedule.pk,
                    artifact_id=None,
                    channel=entry.channel,
                    recipient_id=entry.pk,
                    recipient_type=entry.recipient_type,
                    destination_snapshot=entry.destination,
                    attempt_number=1,
                    status=delivery_status,
                    queued_at=queued_at,
                    attempted_at=attempted_at,
                    delivered_at=delivered_at if delivery_status == ReportDeliveryStatus.DELIVERED else None,
                    failed_at=attempted_at if delivery_status == ReportDeliveryStatus.FAILED else None,
                )
            )
        ReportDelivery.objects.using(db).bulk_create(deliveries, ignore_conflicts=True)


def reverse(apps, schema_editor):
    db = schema_editor.connection.alias

    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")
    ReportSchedule = apps.get_model("reports", "ReportSchedule")
    ReportScheduleRecipient = apps.get_model("reports", "ReportScheduleRecipient")
    ReportJob = apps.get_model("reports", "ReportJob")
    ReportArtifact = apps.get_model("reports", "ReportArtifact")
    ReportDelivery = apps.get_model("reports", "ReportDelivery")

    ReportDelivery.objects.using(db).all().delete()
    ReportArtifact.objects.using(db).all().delete()
    ReportScheduleRecipient.objects.using(db).all().delete()

    ReportJob.objects.using(db).update(
        definition=None,
        schedule=None,
        definition_code_snapshot="",
        definition_version_snapshot=None,
        template_name_snapshot="",
        template_version_snapshot=None,
        template_config_snapshot={},
        parameters_snapshot={},
        scope_snapshot={},
        recipient_snapshot={},
        output_config_snapshot={},
    )
    ReportSchedule.objects.using(db).update(
        definition=None,
        template=None,
        status=ReportScheduleStatus.ACTIVE,
        primary_format=None,
        attachment_formats=[],
        recurrence_rule={},
        consecutive_failure_count=0,
        last_success_at=None,
    )
    ReportTemplate.objects.using(db).update(definition=None, branding_config={}, is_default=False, version=1, created_by=None)
    ReportDefinition.objects.using(db).all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0007_normalize_reporting_domain"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse),
    ]
