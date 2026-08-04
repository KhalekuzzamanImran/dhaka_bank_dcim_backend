from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.audit.models import AuditLog
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.enums import ReportTriggerSource
from apps.reports.models import ReportArtifact, ReportDefinition, ReportJob, ReportJobStatus, ReportTemplate
from apps.reports.services.artifacts import create_report_artifact, get_default_artifact_retention_expires_at
from apps.reports.services.factory import create_report_job
from apps.reports.services.generator import generate_report_job
from apps.reports.services.retention import cleanup_expired_report_artifacts
from apps.reports.tasks import cleanup_expired_report_artifacts_task


@override_settings(
    REPORT_ARTIFACT_RETENTION_DAYS=14,
    REPORT_ARTIFACT_CLEANUP_BATCH_SIZE=2,
    REPORT_ARTIFACT_CLEANUP_ENABLED=True,
)
class ReportPhase5CRetentionTestCase(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="reports-retention-")
        self._override = override_settings(MEDIA_ROOT=self.media_root)
        self._override.enable()

        suffix = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(username=f"retention-user-{suffix}", password="test12345", is_active=True)
        self.org = Organization.objects.create(name=f"Org {suffix}", code=f"ORG-{suffix}")
        self.dc = DataCenter.objects.create(organization=self.org, name=f"DC {suffix}", code=f"DC-{suffix}")
        self.device_type = DeviceType.objects.create(name=f"UPS {suffix}", code=f"UPS{suffix.upper()}", category="POWER")
        self.vendor = Vendor.objects.create(name=f"Vendor {suffix}", code=f"VEN{suffix.upper()}")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name=f"Model {suffix}",
            model_number=f"M1-{suffix}",
        )
        self.device = Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            device_type=self.device_type,
            device_model=self.device_model,
            name=f"UPS-{suffix}",
            code=f"UPS-{suffix}",
        )
        self.definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _template(self, *, primary_format="CSV", attachment_formats=None):
        return ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Retention Template",
            code=f"RETENTION-{uuid.uuid4().hex[:8]}",
            description="Retention test template",
            config={"report_type": "device_inventory", "output_format": primary_format.lower()},
            default_parameters={},
            primary_format=primary_format,
            attachment_formats=attachment_formats or [],
            include_raw_data=False,
            is_active=True,
        )

    def _job(self, template):
        result = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={"report_type": "device_inventory"},
            queue_job=False,
        )
        return result.job

    def _source_file(self, content=b"device_id,device_name\n1,UPS-01\n", suffix=".csv"):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=self.media_root)
        handle.write(content)
        handle.flush()
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.remove(handle.name))
        return handle.name

    def _generated_artifact(self, *, primary_format="CSV", attachment_formats=None, retention_expires_at=None):
        template = self._template(primary_format=primary_format, attachment_formats=attachment_formats)
        job = self._job(template)
        generated = generate_report_job(job.id)
        artifact = generated.artifacts.order_by("created_at").first()
        if retention_expires_at is not None:
            ReportArtifact.objects.filter(pk=artifact.pk).update(retention_expires_at=retention_expires_at)
            artifact.refresh_from_db()
        return generated, artifact

    def test_default_retention_is_calculated_from_settings(self):
        template = self._template()
        job = self._job(template)
        source_path = self._source_file()
        result = create_report_artifact(
            job=job,
            generated_file=SimpleNamespace(path=source_path),
            format="CSV",
            filename="device-inventory.csv",
            content_type="text/csv",
        )
        self.assertTrue(result.created)
        self.assertEqual(result.artifact.retention_expires_at.date(), (timezone.now() + timedelta(days=14)).date())

    def test_explicit_retention_is_preserved(self):
        template = self._template()
        job = self._job(template)
        source_path = self._source_file()
        explicit = timezone.now() + timedelta(days=3)
        result = create_report_artifact(
            job=job,
            generated_file=SimpleNamespace(path=source_path),
            format="CSV",
            filename="device-inventory.csv",
            content_type="text/csv",
            retention_expires_at=explicit,
        )
        self.assertEqual(result.artifact.retention_expires_at, explicit)

    def test_expired_artifact_is_deleted(self):
        generated, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertFalse(default_storage.exists(artifact.file.name))
        self.assertFalse(default_storage.exists(artifact.file.name))

    def test_non_expired_artifact_is_retained(self):
        generated, artifact = self._generated_artifact(retention_expires_at=timezone.now() + timedelta(days=1))
        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)
        self.assertEqual(result["examined"], 0)
        self.assertTrue(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertTrue(default_storage.exists(artifact.file.name))
        self.assertTrue(default_storage.exists(artifact.file.name))

    def test_null_retention_artifact_is_retained(self):
        generated, artifact = self._generated_artifact()
        ReportArtifact.objects.filter(pk=artifact.pk).update(retention_expires_at=None)
        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)
        self.assertEqual(result["examined"], 0)
        self.assertTrue(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertTrue(default_storage.exists(artifact.file.name))
        self.assertTrue(default_storage.exists(artifact.file.name))

    def test_dry_run_does_not_delete(self):
        _, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10, dry_run=True)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertTrue(default_storage.exists(artifact.file.name))

    def test_batch_size_limit_is_respected(self):
        _, first = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=2))
        _, second = self._generated_artifact(primary_format="PDF", attachment_formats=["CSV"], retention_expires_at=timezone.now() - timedelta(days=2))
        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=1)
        self.assertEqual(result["examined"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(ReportArtifact.objects.count(), 2)
        self.assertEqual(ReportArtifact.objects.filter(pk__in=[first.pk, second.pk]).count(), 1)

    def test_storage_deletion_failure_keeps_db_row(self):
        _, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))

        original_delete = artifact.file.storage.delete

        def failing_delete(name):
            raise OSError("storage failure")

        with patch.object(artifact.file.storage, "delete", side_effect=failing_delete):
            result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)

        self.assertEqual(result["failed"], 1)
        self.assertTrue(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertTrue(default_storage.exists(artifact.file.name))
        artifact.refresh_from_db()
        self.assertIsNotNone(artifact.file.name)

    def test_missing_physical_file_is_handled_safely(self):
        _, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        os.remove(artifact.file.path)

        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertFalse(default_storage.exists(artifact.file.name))

    def test_legacy_report_file_mirror_is_cleared_safely(self):
        generated, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)

        generated.refresh_from_db()
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertFalse(default_storage.exists(artifact.file.name))
        self.assertFalse(generated.file)

    def test_unrelated_legacy_file_is_not_deleted(self):
        _, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        unrelated_job = ReportJob.objects.create(
            organization=self.org,
            data_center=self.dc,
            requested_by=self.user,
            status=ReportJobStatus.COMPLETED,
            started_at=timezone.now(),
            completed_at=timezone.now(),
            parameters={"report_type": "device_inventory"},
        )
        unrelated_path = default_storage.save(
            f"reports/legacy/{uuid.uuid4().hex}.txt",
            ContentFile(b"legacy file"),
        )
        unrelated_job.file.name = unrelated_path
        unrelated_job.save(update_fields=["file", "updated_at"])

        cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)

        unrelated_job.refresh_from_db()
        self.assertTrue(default_storage.exists(unrelated_job.file.name))
        self.assertFalse(default_storage.exists(artifact.file.name))

    def test_failed_deletion_does_not_stop_batch(self):
        first_generated, first_artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        second_generated, second_artifact = self._generated_artifact(primary_format="PDF", attachment_formats=["CSV"], retention_expires_at=timezone.now() - timedelta(days=1))
        original_delete = first_artifact.file.storage.delete

        def selective_delete(name):
            if name == first_artifact.file.name:
                raise OSError("first failure")
            return original_delete(name)

        with patch.object(first_artifact.file.storage, "delete", side_effect=selective_delete):
            result = cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)

        first_artifact.refresh_from_db()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertTrue(ReportArtifact.objects.filter(pk=first_artifact.pk).exists())
        self.assertFalse(ReportArtifact.objects.filter(pk=second_artifact.pk).exists())
        self.assertTrue(default_storage.exists(first_artifact.file.name))
        self.assertFalse(default_storage.exists(second_artifact.file.name))

    def test_cleanup_task_uses_service(self):
        with patch("apps.reports.tasks.cleanup_expired_report_artifacts", return_value={"examined": 1, "deleted": 1, "failed": 0, "skipped": 0, "errors": [], "disabled": False, "dry_run": True, "batch_size": 1}) as mocked:
            result = cleanup_expired_report_artifacts_task.apply(kwargs={"dry_run": True, "batch_size": 1}).get()

        self.assertTrue(mocked.called)
        self.assertEqual(result["examined"], 1)

    def test_management_command_dry_run(self):
        _, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        buffer = StringIO()

        call_command("cleanup_report_artifacts", dry_run=True, batch_size=1, stdout=buffer)

        self.assertTrue(ReportArtifact.objects.filter(pk=artifact.pk).exists())
        self.assertTrue(default_storage.exists(artifact.file.name))
        self.assertIn("dry_run=True", buffer.getvalue())

    def test_audit_events_are_written(self):
        _, artifact = self._generated_artifact(retention_expires_at=timezone.now() - timedelta(days=1))
        before_count = AuditLog.objects.count()
        cleanup_expired_report_artifacts(now=timezone.now(), batch_size=10)

        self.assertGreater(AuditLog.objects.count(), before_count)
        self.assertTrue(
            AuditLog.objects.filter(
                resource_type="ReportArtifact",
                resource_id=str(artifact.id),
                organization=self.org,
            ).exists()
        )
