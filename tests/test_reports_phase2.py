from __future__ import annotations

from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from celery.exceptions import Retry
from unittest.mock import patch

from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.domain import ReportJobStatus, ReportJobTriggerSource, ReportScheduleStatus
from apps.reports.models import ReportArtifactStatus, ReportDefinition, ReportJob, ReportSchedule, ReportTemplate
from apps.reports.tasks import deliver_report_task
from apps.reports.scheduling.dispatcher import dispatch_due_report_schedules
from apps.reports.services.execution import ReportExecutionService
from apps.reports.services.generator import generate_report_job
from apps.reports.services.jobs import ReportJobService


class ReportPhase2PipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="report-phase2-user", password="test12345", is_active=True, is_superuser=True, is_staff=True)
        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
        self.vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name="Model",
            model_number="M1",
        )
        self.device = Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            device_type=self.device_type,
            device_model=self.device_model,
            name="UPS-01",
            code="UPS-01",
        )
        self.definition = ReportDefinition.objects.get(code="device_inventory")
        self.template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Device Inventory Template",
            code="DEVICE-INVENTORY",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.user,
        )

    def _manual_job(self, *, enqueue=False):
        return ReportJobService.create_manual_job(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=self.template,
            parameters={"report_type": "device_inventory"},
            primary_format="CSV",
            attachment_formats=[],
            requested_by=self.user,
            recipients=[],
            enqueue=enqueue,
        )

    def test_manual_job_creation_persists_snapshots(self):
        job = self._manual_job(enqueue=False)
        self.assertEqual(job.status, ReportJobStatus.QUEUED)
        self.assertEqual(job.definition_code_snapshot, "device_inventory")
        self.assertEqual(job.template_name_snapshot, "Device Inventory Template")
        self.assertEqual(job.output_config_snapshot["primary_format"], "CSV")

    def test_execution_creates_artifact_and_syncs_legacy_file(self):
        job = self._manual_job(enqueue=False)
        generated = ReportExecutionService.execute_job(job.id)
        generated.refresh_from_db()
        self.assertEqual(generated.status, ReportJobStatus.SUCCEEDED)
        self.assertTrue(generated.primary_artifact)
        self.assertEqual(generated.primary_artifact.status, ReportArtifactStatus.AVAILABLE)
        self.assertTrue(generated.file)

    def test_compatibility_wrapper_still_generates_report(self):
        job = self._manual_job(enqueue=False)
        generated = generate_report_job(job.id)
        generated.refresh_from_db()
        self.assertEqual(generated.status, ReportJobStatus.SUCCEEDED)
        self.assertTrue(generated.file)

    def test_duplicate_scheduled_execution_returns_existing_job(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=self.template,
            name="Nightly Inventory",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time="06:00:00",
            primary_format="CSV",
            attachment_formats=[],
            recurrence_rule={"frequency": "DAILY", "delivery_time": "06:00:00"},
            status=ReportScheduleStatus.ACTIVE,
            start_at=timezone.now() - timedelta(days=1),
            parameters={"report_type": "device_inventory"},
            recipients=["ops@example.com"],
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now(),
        )
        first = ReportJobService.create_scheduled_job(schedule=schedule, scheduled_for=schedule.next_run_at, enqueue=False)
        second = ReportJobService.create_scheduled_job(schedule=schedule, scheduled_for=schedule.next_run_at, enqueue=False)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ReportJob.objects.filter(schedule=schedule, scheduled_for=schedule.next_run_at).count(), 1)

    def test_retry_job_creates_child_job_without_schedule_timestamp_collision(self):
        job = self._manual_job(enqueue=False)
        job.started_at = timezone.now()
        job.completed_at = timezone.now()
        job.status = ReportJobStatus.FAILED
        job.error_message = "boom"
        job.save(update_fields=["started_at", "completed_at", "status", "error_message", "updated_at"])

        retry_job = ReportJobService.create_retry_job(failed_job=job, requested_by=self.user, enqueue=False)
        self.assertEqual(retry_job.parent_job_id, job.id)
        self.assertEqual(retry_job.retry_count, 1)
        self.assertIsNone(retry_job.scheduled_for)

    def test_dispatch_due_report_schedules_advances_next_run(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=self.template,
            name="Inventory Dispatch",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time="06:00:00",
            primary_format="CSV",
            attachment_formats=[],
            recurrence_rule={"frequency": "DAILY", "delivery_time": "06:00:00"},
            status=ReportScheduleStatus.ACTIVE,
            start_at=timezone.now() - timedelta(days=1),
            parameters={"report_type": "device_inventory"},
            recipients=["ops@example.com"],
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )

        jobs = dispatch_due_report_schedules(limit=10)
        schedule.refresh_from_db()
        self.assertEqual(len(jobs), 1)
        self.assertGreater(schedule.next_run_at, timezone.now() - timedelta(minutes=1))
        self.assertEqual(schedule.last_run_at is not None, True)
        self.assertEqual(jobs[0].trigger_source, ReportJobTriggerSource.SCHEDULED)

    def test_report_delivery_task_retries_transient_sms_failures(self):
        delivery = type(
            "DeliveryStub",
            (),
            {
                "status": "FAILED",
                "error_message": "HTTPSConnectionPool(host='uatapi.dhakabank.com.bd', port=443): Max retries exceeded with url: /DBLSmsServices/SmsServices.asmx (Caused by ConnectTimeoutError(... connect timeout=30))",
            },
        )()

        with patch("apps.reports.tasks.deliver_report_task_impl", return_value=delivery):
            with self.assertRaises(Retry):
                deliver_report_task.apply(args=["delivery-1"]).get()
