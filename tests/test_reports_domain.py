from __future__ import annotations

import hashlib
import os

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.domain import (
    ReportArtifactStatus,
    ReportArtifactType,
    ReportDeliveryStatus,
    ReportJobStatus,
    ReportJobTriggerSource,
    ReportRecipientChannel,
    ReportRecipientType,
    ReportScheduleStatus,
)
from apps.reports.models import (
    ReportArtifact,
    ReportDelivery,
    ReportDefinition,
    ReportJob,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportTemplate,
)


class ReportDomainModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="report-domain-user", password="test12345", is_active=True)
        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.other_org = Organization.objects.create(name="Org Two", code="ORG-2")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code="DC-2")
        self.device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
        self.vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name="Model",
            model_number="M1",
        )

    def _definition(self, code="telemetry_export") -> ReportDefinition:
        return ReportDefinition.objects.get(code=code)

    def _template(self, *, organization=None, definition=None, code="TEMPLATE-1", config=None, version=1):
        definition = definition or self._definition()
        return ReportTemplate.objects.create(
            organization=organization,
            definition=definition,
            name=f"{definition.name} Template",
            code=code,
            description="Test template",
            config=config if config is not None else {"report_type": definition.code},
            branding_config={"logo": "bank"},
            version=version,
            is_active=True,
            created_by=self.user,
        )

    def _schedule(self, *, organization=None, data_center=None, definition=None, template=None, status=ReportScheduleStatus.ACTIVE):
        definition = definition or self._definition()
        template = template or self._template(organization=organization, definition=definition)
        organization = organization or self.org
        return ReportSchedule.objects.create(
            organization=organization,
            data_center=data_center,
            definition=definition,
            template=template,
            name="Scheduled Report",
            report_type=definition.code,
            frequency="DAILY",
            delivery_time="06:00:00",
            primary_format="CSV",
            attachment_formats=["CSV"],
            recurrence_rule={"frequency": "DAILY", "delivery_time": "06:00:00"},
            status=status,
            start_at=timezone.now(),
            parameters={"date_from": "2026-07-01", "date_to": "2026-07-30"},
            recipients=["ops@example.com"],
            sms_recipients=["01610000000"],
            send_sms=True,
            attach_raw_data=True,
            is_active=status == ReportScheduleStatus.ACTIVE,
            created_by=self.user,
        )

    def _job(self, *, organization=None, data_center=None, definition=None, template=None, schedule=None, status=ReportJobStatus.QUEUED, trigger_source=ReportJobTriggerSource.MANUAL, scheduled_for=None):
        definition = definition or self._definition()
        if template is None and schedule is not None and schedule.template_id:
            template = schedule.template
        if template is None:
            template = self._template(organization=organization, definition=definition)
        organization = organization or self.org
        job = ReportJob(
            organization=organization,
            data_center=data_center,
            definition=definition,
            template=template,
            schedule=schedule,
            trigger_source=trigger_source,
            scheduled_for=scheduled_for,
            status=status,
            requested_by=self.user,
            queued_at=timezone.now(),
            parameters={"report_type": definition.code},
        )
        if status in {ReportJobStatus.RUNNING, ReportJobStatus.PROCESSING, ReportJobStatus.SUCCEEDED, ReportJobStatus.PARTIALLY_SUCCEEDED, ReportJobStatus.FAILED, ReportJobStatus.CANCELLED, ReportJobStatus.COMPLETED}:
            job.started_at = timezone.now()
        if status in {ReportJobStatus.SUCCEEDED, ReportJobStatus.PARTIALLY_SUCCEEDED, ReportJobStatus.FAILED, ReportJobStatus.CANCELLED, ReportJobStatus.COMPLETED}:
            job.completed_at = timezone.now()
        return job

    def tearDown(self):
        for artifact in ReportArtifact.objects.all():
            if artifact.file:
                try:
                    path = artifact.file.path
                    artifact.file.delete(save=False)
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                except Exception:
                    pass
        for job in ReportJob.objects.all():
            if job.file:
                try:
                    path = job.file.path
                    job.file.delete(save=False)
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                except Exception:
                    pass

    def test_report_definition_seed_exists_and_duplicate_code_is_rejected(self):
        self.assertTrue(ReportDefinition.objects.filter(code="telemetry_export").exists())
        definition = self._definition()
        duplicate = ReportDefinition(
            code=definition.code,
            name=definition.name,
            category=definition.category,
            handler_key=definition.handler_key,
            parameter_schema=definition.parameter_schema,
            supported_formats=definition.supported_formats,
            supports_scheduling=definition.supports_scheduling,
            supports_raw_attachment=definition.supports_raw_attachment,
            supports_charts=definition.supports_charts,
            required_permission=definition.required_permission,
            version=definition.version,
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_template_definition_consistency_and_global_templates(self):
        global_template = self._template(organization=None, code="GLOBAL-TELEMETRY")
        self.assertIsNone(global_template.organization_id)
        self.assertEqual(global_template.definition.code, "telemetry_export")

        mismatched = ReportTemplate(
            organization=self.org,
            definition=self._definition("alert_summary"),
            name="Mismatch",
            code="MISMATCH",
            config={"report_type": "telemetry_export"},
            branding_config={},
            version=1,
            is_active=True,
            created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            mismatched.full_clean()

    def test_schedule_scope_validation_and_recipient_duplicate_prevention(self):
        template = self._template(organization=self.org)
        schedule = ReportSchedule(
            organization=self.org,
            data_center=self.other_dc,
            definition=self._definition(),
            template=template,
            name="Invalid Scope",
            report_type="telemetry_export",
            frequency="DAILY",
            delivery_time="06:00:00",
            primary_format="CSV",
            attachment_formats=["CSV"],
            recurrence_rule={"frequency": "DAILY", "delivery_time": "06:00:00"},
            status=ReportScheduleStatus.ACTIVE,
            parameters={},
            recipients=["ops@example.com"],
            sms_recipients=["01610000000"],
            send_sms=True,
            created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            schedule.full_clean()

        schedule = self._schedule(template=template, data_center=self.dc)
        recipient = ReportScheduleRecipient.objects.create(
            schedule=schedule,
            channel=ReportRecipientChannel.EMAIL,
            recipient_type=ReportRecipientType.TO,
            destination="ops@example.com",
            display_name="Operations",
            is_active=True,
        )
        self.assertEqual(recipient.destination, "ops@example.com")

        duplicate = ReportScheduleRecipient(
            schedule=schedule,
            channel=ReportRecipientChannel.EMAIL,
            recipient_type=ReportRecipientType.TO,
            destination="ops@example.com",
            display_name="Operations Duplicate",
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        invalid_sms = ReportScheduleRecipient(
            schedule=schedule,
            channel=ReportRecipientChannel.SMS,
            recipient_type=ReportRecipientType.CC,
            destination="",
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            invalid_sms.full_clean()

    def test_job_trigger_status_compatibility_and_immutable_snapshots(self):
        template = self._template(organization=self.org, code="JOB-SNAPSHOT")
        job = self._job(
            template=template,
            status=ReportJobStatus.SUCCEEDED,
            trigger_source=ReportJobTriggerSource.RUN_NOW,
        )
        job.save()

        self.assertEqual(job.legacy_status, ReportJobStatus.COMPLETED)
        self.assertEqual(job.definition_code_snapshot, template.definition.code)
        self.assertEqual(job.template_name_snapshot, template.name)

        template.name = "Updated Template Name"
        template.version = 2
        template.save()

        job.refresh_from_db()
        self.assertEqual(job.definition_code_snapshot, template.definition.code)
        self.assertEqual(job.template_name_snapshot, "Telemetry Export Template")

    def test_scheduled_execution_uniqueness(self):
        schedule = self._schedule(data_center=self.dc)
        scheduled_for = schedule.next_run_at
        first = self._job(schedule=schedule, status=ReportJobStatus.QUEUED, trigger_source=ReportJobTriggerSource.SCHEDULED, scheduled_for=scheduled_for)
        first.save()

        duplicate = self._job(schedule=schedule, status=ReportJobStatus.QUEUED, trigger_source=ReportJobTriggerSource.SCHEDULED, scheduled_for=scheduled_for)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_artifact_metadata_delivery_lifecycle_and_legacy_file_compatibility(self):
        job = self._job(status=ReportJobStatus.SUCCEEDED, trigger_source=ReportJobTriggerSource.MANUAL)
        job.save()

        legacy_file = ContentFile(b"legacy report bytes", name="legacy.csv")
        job.file.save("legacy.csv", legacy_file, save=True)
        job.refresh_from_db()
        self.assertTrue(job.is_downloadable)
        self.assertIsNone(job.primary_artifact)

        artifact = ReportArtifact.objects.create(
            job=job,
            artifact_type=ReportArtifactType.PRIMARY,
            format="CSV",
            file=ContentFile(b"artifact bytes", name="artifact.csv"),
            status=ReportArtifactStatus.AVAILABLE,
        )
        artifact.refresh_from_db()
        self.assertEqual(artifact.file_name, "artifact.csv")
        self.assertEqual(artifact.size_bytes, len(b"artifact bytes"))
        self.assertEqual(artifact.checksum_sha256, hashlib.sha256(b"artifact bytes").hexdigest())

        delivery = ReportDelivery.objects.create(
            job=job,
            schedule=None,
            artifact=artifact,
            channel=ReportRecipientChannel.EMAIL,
            recipient=None,
            recipient_type=ReportRecipientType.TO,
            destination_snapshot="ops@example.com",
            attempt_number=1,
            status=ReportDeliveryStatus.PENDING,
        )
        delivery.status = ReportDeliveryStatus.DELIVERED
        delivery.delivered_at = timezone.now()
        delivery.save()
        self.assertEqual(delivery.status, ReportDeliveryStatus.DELIVERED)

    def test_cross_organization_validation(self):
        other_definition = self._definition()
        other_template = self._template(organization=self.other_org, code="OTHER-ORG-TEMPLATE", definition=other_definition)
        job = ReportJob(
            organization=self.org,
            data_center=None,
            definition=other_definition,
            template=other_template,
            trigger_source=ReportJobTriggerSource.MANUAL,
            requested_by=self.user,
            queued_at=timezone.now(),
            status=ReportJobStatus.QUEUED,
            parameters={"report_type": other_definition.code},
        )
        with self.assertRaises(ValidationError):
            job.full_clean()
