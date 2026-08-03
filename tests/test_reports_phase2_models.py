from __future__ import annotations

import importlib
from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.definition_seeds import REPORT_DEFINITION_SEEDS
from apps.reports.models import (
    ReportArtifact,
    ReportDefinition,
    ReportDelivery,
    ReportDeliveryStatus,
    ReportJob,
    ReportJobStatus,
    ReportRecipientChannel,
    ReportTemplate,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportScheduleRun,
    ReportScheduleStatus,
    ReportTriggerSource,
)
from apps.reports.services.definitions import seed_report_definitions
from apps.reports.services.schedules import claim_due_report_schedules, execute_report_schedule


class ReportPhase2ModelTestCase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.other_org = Organization.objects.create(name="Org Two", code="ORG-2")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code="DC-2")
        self.user = User.objects.create_user(username="report-phase2-user", password="test12345", is_active=True)
        self.device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
        self.vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name="Model",
            model_number="M1",
        )

    def _template(
        self,
        *,
        organization=None,
        code="TEMPLATE-1",
        config=None,
        definition=None,
        default_parameters=None,
        primary_format=None,
        attachment_formats=None,
        is_active=True,
    ):
        organization = organization or self.org
        config = config or {"report_type": "device_inventory", "output_format": "csv"}
        return ReportTemplate.objects.create(
            organization=organization,
            definition=definition,
            name=code.replace("-", " ").title(),
            code=code,
            description="Test template",
            config=config,
            default_parameters=default_parameters if default_parameters is not None else {},
            primary_format=primary_format,
            attachment_formats=attachment_formats if attachment_formats is not None else [],
            is_active=is_active,
        )

    def _schedule(self, *, organization=None, data_center=None, template=None, status=ReportScheduleStatus.ACTIVE, recipients=None, sms_recipients=None, send_sms=False):
        organization = organization or self.org
        return ReportSchedule.objects.create(
            organization=organization,
            data_center=data_center,
            template=template,
            name="Schedule One",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameters={"report_type": "device_inventory"},
            recipients=recipients if recipients is not None else ["ops@example.com"],
            send_sms=send_sms,
            sms_recipients=sms_recipients if sms_recipients is not None else [],
            is_active=status == ReportScheduleStatus.ACTIVE,
            status=status,
            created_by=self.user,
        )

    def _complete_generated_job(self, job_id):
        job = ReportJob.objects.get(pk=job_id)
        started_at = timezone.now()
        job.status = ReportJobStatus.COMPLETED
        job.started_at = started_at
        job.completed_at = started_at + timedelta(seconds=1)
        job.file = SimpleUploadedFile("report.csv", b"header\nvalue\n", content_type="text/csv")
        job.save()
        return ReportJob.objects.get(pk=job_id)

    def test_report_definition_seeding_is_idempotent(self):
        first = seed_report_definitions()
        second = seed_report_definitions()

        self.assertEqual(ReportDefinition.objects.count(), len(REPORT_DEFINITION_SEEDS))
        self.assertEqual(first["created"] + first["updated"], len(REPORT_DEFINITION_SEEDS))
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], len(REPORT_DEFINITION_SEEDS))

    def test_schedule_status_and_is_active_remain_mirrored(self):
        schedule = self._schedule(status=ReportScheduleStatus.PAUSED)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ReportScheduleStatus.PAUSED)
        self.assertFalse(schedule.is_active)

        schedule.status = ReportScheduleStatus.ACTIVE
        schedule.is_active = False
        schedule.save(update_fields={"status", "is_active"})
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ReportScheduleStatus.ACTIVE)
        self.assertTrue(schedule.is_active)

    def test_claim_due_report_schedules_uses_canonical_status(self):
        active_schedule = self._schedule(status=ReportScheduleStatus.ACTIVE)
        paused_schedule = self._schedule(status=ReportScheduleStatus.PAUSED)
        disabled_schedule = self._schedule(status=ReportScheduleStatus.DISABLED)

        due_time = timezone.now() - timedelta(minutes=10)
        ReportSchedule.objects.filter(pk__in=[active_schedule.pk, paused_schedule.pk, disabled_schedule.pk]).update(next_run_at=due_time)

        claimed = claim_due_report_schedules(limit=10)
        self.assertEqual([entry.schedule_id for entry in claimed], [str(active_schedule.pk)])
        active_schedule.refresh_from_db()
        self.assertIsNotNone(active_schedule.last_run_at)
        self.assertGreaterEqual(active_schedule.next_run_at, active_schedule.last_run_at)

    def test_schedule_template_must_belong_to_same_organization(self):
        template = self._template(organization=self.other_org, code="OTHER-TEMPLATE")
        schedule = ReportSchedule(
            organization=self.org,
            data_center=self.dc,
            template=template,
            name="Bad Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameters={"report_type": "device_inventory"},
            recipients=["ops@example.com"],
            created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            schedule.full_clean()

    def test_report_schedule_recipient_channel_validation(self):
        email_recipient = ReportScheduleRecipient(
            schedule=self._schedule(),
            channel=ReportRecipientChannel.EMAIL,
            display_name="Ops",
        )
        with self.assertRaises(ValidationError):
            email_recipient.full_clean()

        sms_recipient = ReportScheduleRecipient(
            schedule=self._schedule(),
            channel=ReportRecipientChannel.SMS,
            display_name="Ops SMS",
        )
        with self.assertRaises(ValidationError):
            sms_recipient.full_clean()

    def test_schedule_run_duplicate_constraint_blocks_same_window(self):
        schedule = self._schedule()
        run_time = schedule.calculate_next_run_at()
        ReportScheduleRun.objects.create(
            schedule=schedule,
            organization=self.org,
            requested_by=self.user,
            window_start=run_time - timedelta(hours=1),
            window_end=run_time,
            scheduled_for=run_time,
            trigger_source=ReportTriggerSource.SCHEDULED,
        )
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                ReportScheduleRun.objects.create(
                    schedule=schedule,
                    organization=self.org,
                    requested_by=self.user,
                    window_start=run_time,
                    window_end=run_time,
                    scheduled_for=run_time,
                    trigger_source=ReportTriggerSource.SCHEDULED,
                )

    @patch("apps.reports.services.schedules._send_report_email")
    @patch("apps.reports.services.schedules.generate_report_job")
    def test_automatic_scheduled_runs_dedupe_by_planned_window(self, mock_generate_report_job, mock_send_report_email):
        seed_report_definitions()
        definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")
        template = self._template(
            definition=definition,
            default_parameters={"template_default": "alpha", "shared": "template"},
            config={"report_type": "device_inventory", "output_format": "csv"},
        )
        schedule = self._schedule(template=template)
        schedule.parameter_overrides = {"shared": "override", "schedule_only": "beta"}
        schedule.save(update_fields={"parameter_overrides"})

        mock_generate_report_job.side_effect = self._complete_generated_job

        scheduled_for_one = (timezone.now() + timedelta(hours=1)).replace(microsecond=0)
        result_one = execute_report_schedule(
            str(schedule.pk),
            window_start=(scheduled_for_one - timedelta(days=1)).isoformat(),
            window_end=scheduled_for_one.isoformat(),
            scheduled_for=scheduled_for_one.isoformat(),
            trigger_source="SCHEDULED",
        )
        result_one.refresh_from_db()
        self.assertEqual(ReportScheduleRun.objects.filter(schedule=schedule).count(), 1)
        self.assertEqual(ReportJob.objects.filter(schedule=schedule).count(), 1)

        result_duplicate = execute_report_schedule(
            str(schedule.pk),
            window_start=(scheduled_for_one - timedelta(days=1)).isoformat(),
            window_end=scheduled_for_one.isoformat(),
            scheduled_for=scheduled_for_one.isoformat(),
            trigger_source="SCHEDULED",
        )
        self.assertEqual(result_duplicate.pk, schedule.pk)
        self.assertEqual(ReportScheduleRun.objects.filter(schedule=schedule).count(), 1)
        self.assertEqual(ReportJob.objects.filter(schedule=schedule).count(), 1)

        scheduled_for_two = scheduled_for_one + timedelta(days=1)
        result_two = execute_report_schedule(
            str(schedule.pk),
            window_start=scheduled_for_two.isoformat(),
            window_end=(scheduled_for_two + timedelta(hours=1)).isoformat(),
            scheduled_for=scheduled_for_two.isoformat(),
            trigger_source="SCHEDULED",
        )
        result_two.refresh_from_db()
        self.assertEqual(ReportScheduleRun.objects.filter(schedule=schedule).count(), 2)
        self.assertEqual(ReportJob.objects.filter(schedule=schedule).count(), 2)
        self.assertTrue(mock_send_report_email.called)

        manual_window_end = timezone.now().replace(microsecond=0)
        manual_result = execute_report_schedule(
            str(schedule.pk),
            window_start=(manual_window_end - timedelta(hours=1)).isoformat(),
            window_end=manual_window_end.isoformat(),
            trigger_source="MANUAL",
        )
        manual_result.refresh_from_db()
        self.assertEqual(ReportScheduleRun.objects.filter(schedule=schedule).count(), 3)
        self.assertEqual(ReportJob.objects.filter(schedule=schedule).count(), 3)

    @patch("apps.reports.services.schedules._send_report_email")
    @patch("apps.reports.services.schedules.generate_report_job")
    def test_template_backed_schedule_uses_template_defaults_and_snapshots(self, mock_generate_report_job, mock_send_report_email):
        seed_report_definitions()
        definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")
        template = self._template(
            definition=definition,
            default_parameters={"template_default": "alpha", "shared": "template"},
            config={"report_type": "device_inventory", "output_format": "csv"},
            primary_format="CSV",
            attachment_formats=["CSV"],
        )
        schedule = self._schedule(template=template)
        schedule.parameter_overrides = {"shared": "override", "schedule_only": "beta"}
        schedule.save(update_fields={"parameter_overrides"})

        mock_generate_report_job.side_effect = self._complete_generated_job
        scheduled_for = (timezone.now() + timedelta(hours=2)).replace(microsecond=0)
        result = execute_report_schedule(
            str(schedule.pk),
            window_start=(scheduled_for - timedelta(days=1)).isoformat(),
            window_end=scheduled_for.isoformat(),
            scheduled_for=scheduled_for.isoformat(),
            trigger_source="SCHEDULED",
        )
        result.refresh_from_db()
        job = result.last_job
        self.assertIsNotNone(job)
        job.refresh_from_db()
        self.assertEqual(job.template_id, template.id)
        self.assertEqual(job.definition_id, definition.id)
        self.assertEqual(job.parameters["template_default"], "alpha")
        self.assertEqual(job.parameters["shared"], "override")
        self.assertEqual(job.parameters["schedule_only"], "beta")
        self.assertEqual(job.parameters["report_type"], "device_inventory")
        self.assertEqual(job.parameters_snapshot["shared"], "override")
        self.assertEqual(job.template_snapshot["code"], template.code)

        template.default_parameters = {"template_default": "changed"}
        template.save()
        job.refresh_from_db()
        self.assertEqual(job.parameters_snapshot["template_default"], "alpha")
        self.assertEqual(job.template_snapshot["default_parameters"]["template_default"], "alpha")

    @patch("apps.reports.services.schedules._send_report_email")
    @patch("apps.reports.services.schedules.generate_report_job")
    def test_legacy_schedule_without_template_keeps_compatibility_path(self, mock_generate_report_job, mock_send_report_email):
        schedule = self._schedule(template=None)
        schedule.parameters = {"legacy_default": "value"}
        schedule.save(update_fields={"parameters"})

        mock_generate_report_job.side_effect = self._complete_generated_job
        result = execute_report_schedule(str(schedule.pk), trigger_source="MANUAL")
        result.refresh_from_db()
        job = result.last_job
        self.assertIsNotNone(job)
        job.refresh_from_db()
        self.assertIsNone(job.template_id)
        self.assertIsNone(job.definition_id)
        self.assertEqual(job.parameters["legacy_default"], "value")

    def test_report_job_idempotency_key_validation_rejects_blank_values(self):
        base_kwargs = {
            "organization": self.org,
            "requested_by": self.user,
            "status": ReportJobStatus.PENDING,
            "parameters": {"report_type": "device_inventory"},
            "parameters_snapshot": {},
            "template_snapshot": {},
            "output_config_snapshot": {},
            "recipient_snapshot": {},
            "scope_snapshot": {},
            "template_config_snapshot": {},
            "source_event_snapshot": {},
            "trigger_source": ReportTriggerSource.MANUAL,
        }

        null_key_job = ReportJob.objects.create(idempotency_key=None, **base_kwargs)
        self.assertIsNone(null_key_job.idempotency_key)

        valid_key_job = ReportJob.objects.create(idempotency_key="manual-key-accepted", **base_kwargs)
        self.assertEqual(valid_key_job.idempotency_key, "manual-key-accepted")

        with self.assertRaises(ValidationError):
            ReportJob(idempotency_key="", **base_kwargs).full_clean()

        with self.assertRaises(ValidationError):
            ReportJob(idempotency_key="   ", **base_kwargs).full_clean()

    def test_legacy_recipient_migration_skips_invalid_and_dedupes_contacts(self):
        migration = importlib.import_module(
            "apps.reports.migrations.0013_seed_definitions_and_backfill_structured_reporting"
        )

        email_values = ["ops@example.com", " ops@example.com ", "omar@adn", "", "   "]
        sms_values = ["01329665857", " 01329665857 ", "", None]

        self.assertEqual(
            migration._normalize_legacy_email_recipients(email_values, schedule_id="schedule-1"),
            ["ops@example.com"],
        )
        self.assertEqual(
            migration._normalize_legacy_sms_recipients(sms_values, schedule_id="schedule-1"),
            ["01329665857"],
        )

    def test_report_job_snapshot_fields_persist(self):
        template = self._template()
        definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")
        schedule = self._schedule(template=template)
        job = ReportJob.objects.create(
            organization=self.org,
            data_center=self.dc,
            definition=definition,
            template=template,
            schedule=schedule,
            requested_by=self.user,
            status=ReportJobStatus.QUEUED,
            parameters={"report_type": "device_inventory"},
            parameters_snapshot={"report_type": "device_inventory"},
            template_snapshot={"code": template.code},
            output_config_snapshot={"primary_format": "CSV"},
            recipient_snapshot={"emails": ["ops@example.com"]},
            scope_snapshot={"organization_id": str(self.org.id)},
            source_event_snapshot={"source": "manual"},
            trigger_source=ReportTriggerSource.MANUAL,
            idempotency_key="manual-key-1",
        )

        self.assertEqual(job.status, ReportJobStatus.QUEUED)
        self.assertEqual(job.template_snapshot["code"], template.code)
        self.assertEqual(job.source_event_snapshot["source"], "manual")

    def test_report_artifact_relationship_and_delivery_statuses(self):
        template = self._template()
        job = ReportJob.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            status=ReportJobStatus.COMPLETED,
            started_at=timezone.now(),
            completed_at=timezone.now() + timedelta(seconds=5),
            parameters={"report_type": "device_inventory"},
            parameters_snapshot={"report_type": "device_inventory"},
            template_snapshot={"code": template.code},
            output_config_snapshot={"primary_format": "CSV"},
            recipient_snapshot={"emails": ["ops@example.com"]},
            scope_snapshot={"organization_id": str(self.org.id)},
            source_event_snapshot={},
            trigger_source=ReportTriggerSource.MANUAL,
            idempotency_key="manual-key-2",
        )

        artifact = ReportArtifact.objects.create(
            job=job,
            format="CSV",
            file=SimpleUploadedFile("report.csv", b"header\nvalue\n", content_type="text/csv"),
            original_filename="report.csv",
            content_type="text/csv",
            size_bytes=12,
            checksum_sha256="a" * 64,
        )
        self.assertEqual(job.artifacts.count(), 1)
        self.assertEqual(artifact.job_id, job.id)

        delivery = ReportDelivery.objects.create(
            job=job,
            channel="EMAIL",
            recipient="ops@example.com",
            status=ReportDeliveryStatus.QUEUED,
            queued_at=timezone.now(),
            provider_response={},
        )
        self.assertEqual(delivery.status, ReportDeliveryStatus.QUEUED)
        delivery.status = ReportDeliveryStatus.SENT
        delivery.sent_at = timezone.now()
        delivery.save()
        self.assertEqual(delivery.status, ReportDeliveryStatus.SENT)

    def test_org_and_data_center_relationship_validation(self):
        template = self._template()
        with self.assertRaises(ValidationError):
            ReportJob(
                organization=self.org,
                data_center=self.other_dc,
                template=template,
                requested_by=self.user,
                parameters={"report_type": "device_inventory"},
            ).full_clean()
