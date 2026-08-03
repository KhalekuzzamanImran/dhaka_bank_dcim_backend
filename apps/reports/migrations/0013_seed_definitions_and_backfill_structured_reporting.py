from __future__ import annotations

import logging

from django.db import migrations
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.reports.definition_seeds import REPORT_DEFINITION_SEEDS


REPORT_TYPE_TO_DEFINITION_CODE = {
    "device_inventory": "DEVICE_INVENTORY",
    "telemetry_export": "TELEMETRY_EXPORT",
    "alert_summary": "ALERT_SUMMARY",
    "alert_export": "ALERT_DETAIL",
    "notification_delivery": "NOTIFICATION_DELIVERY",
    "audit_export": "AUDIT_EXPORT",
    "room_environment": "ENVIRONMENTAL_TREND",
}

logger = logging.getLogger(__name__)


def _normalize_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]
    normalized = []
    seen = set()
    for item in iterable:
        candidate = str(item).strip()
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_legacy_email_recipients(value, *, schedule_id):
    normalized = []
    seen = set()
    for raw_value in _normalize_list(value):
        candidate = str(raw_value).strip()
        if not candidate:
            continue
        try:
            validate_email(candidate)
        except ValidationError:
            logger.warning(
                "Skipping invalid legacy report email recipient schedule_id=%s value=%r",
                schedule_id,
                raw_value,
            )
            continue
        dedupe_key = candidate.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(candidate)
    return normalized


def _normalize_legacy_sms_recipients(value, *, schedule_id):
    normalized = []
    seen = set()
    for raw_value in _normalize_list(value):
        candidate = str(raw_value).strip()
        if not candidate or candidate.lower() == "none":
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def forwards(apps, schema_editor):
    ReportDefinition = apps.get_model("reports", "ReportDefinition")
    ReportTemplate = apps.get_model("reports", "ReportTemplate")
    ReportSchedule = apps.get_model("reports", "ReportSchedule")
    ReportScheduleRecipient = apps.get_model("reports", "ReportScheduleRecipient")
    ReportJob = apps.get_model("reports", "ReportJob")
    ReportScheduleRun = apps.get_model("reports", "ReportScheduleRun")

    for seed in REPORT_DEFINITION_SEEDS:
        code = seed["code"]
        defaults = {key: value for key, value in seed.items() if key != "code"}
        ReportDefinition.objects.update_or_create(code=code, defaults=defaults)

    definitions_by_code = {
        definition.code: definition
        for definition in ReportDefinition.objects.all().only("id", "code")
    }
    templates_by_id = {
        template.id: template
        for template in ReportTemplate.objects.all().only("id", "name", "code", "version", "config", "definition_id", "default_parameters", "primary_format", "attachment_formats")
    }

    for template in templates_by_id.values():
        config = template.config if isinstance(template.config, dict) else {}
        report_type = config.get("report_type")
        definition_code = REPORT_TYPE_TO_DEFINITION_CODE.get(str(report_type).strip()) if report_type else None
        updates = {}
        if not template.definition_id and definition_code in definitions_by_code:
            updates["definition_id"] = definitions_by_code[definition_code].id
        if not template.default_parameters and isinstance(config.get("default_parameters"), dict):
            updates["default_parameters"] = config.get("default_parameters") or {}
        if not template.primary_format:
            output_format = config.get("output_format") or config.get("primary_format")
            if output_format:
                updates["primary_format"] = str(output_format).strip().upper()
        if not template.attachment_formats and isinstance(config.get("attachment_formats"), (list, tuple)):
            updates["attachment_formats"] = [str(value).strip().upper() for value in _normalize_list(config.get("attachment_formats"))]
        if updates:
            ReportTemplate.objects.filter(pk=template.pk).update(**updates)

    for schedule in ReportSchedule.objects.all().only("id", "recipients", "sms_recipients"):
        existing = {
            (recipient.channel, recipient.email_address or "", recipient.phone_number or "")
            for recipient in ReportScheduleRecipient.objects.filter(schedule_id=schedule.pk).only("channel", "email_address", "phone_number")
        }

        email_recipients = _normalize_legacy_email_recipients(schedule.recipients, schedule_id=schedule.pk)
        sms_recipients = _normalize_legacy_sms_recipients(schedule.sms_recipients, schedule_id=schedule.pk)

        for email_address in email_recipients:
            key = ("EMAIL", email_address, "")
            if key in existing:
                continue
            ReportScheduleRecipient.objects.create(
                schedule_id=schedule.pk,
                channel="EMAIL",
                display_name=email_address,
                email_address=email_address,
                phone_number=None,
                is_active=True,
            )
            existing.add(key)

        for phone_number in sms_recipients:
            key = ("SMS", "", phone_number)
            if key in existing:
                continue
            ReportScheduleRecipient.objects.create(
                schedule_id=schedule.pk,
                channel="SMS",
                display_name=phone_number,
                email_address=None,
                phone_number=phone_number,
                is_active=True,
            )
            existing.add(key)

    for job in ReportJob.objects.all().only("id", "template_id", "parameters", "definition_id", "template_snapshot"):
        updates = {}
        if not job.definition_id and job.template_id:
            template = templates_by_id.get(job.template_id)
            template_definition_id = getattr(template, "definition_id", None)
            if template_definition_id:
                updates["definition_id"] = template_definition_id
        if not job.template_snapshot and job.template_id:
            template = templates_by_id.get(job.template_id)
            if template:
                updates["template_snapshot"] = {
                    "id": str(template.pk),
                    "name": template.name,
                    "code": template.code,
                    "version": template.version,
                    "config": template.config,
                }
        if updates:
            ReportJob.objects.filter(pk=job.pk).update(**updates)

    for run in ReportScheduleRun.objects.all().only("id", "job_id", "generated_job_id"):
        if not run.job_id and run.generated_job_id:
            ReportScheduleRun.objects.filter(pk=run.pk).update(job_id=run.generated_job_id)

    for schedule in ReportSchedule.objects.all().only("id", "status", "is_active"):
        updates = {}
        if schedule.status not in {"ACTIVE", "PAUSED", "DISABLED"}:
            updates["status"] = "ACTIVE" if schedule.is_active else "PAUSED"
        elif schedule.status == "ACTIVE" and not schedule.is_active:
            updates["is_active"] = True
        elif schedule.status in {"PAUSED", "DISABLED"} and schedule.is_active:
            updates["is_active"] = False
        if updates:
            ReportSchedule.objects.filter(pk=schedule.pk).update(**updates)


def backwards(apps, schema_editor):
    # Keep historical data intact; no reverse migration is attempted.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0012_reportartifact_reportdefinition_reportdelivery_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
