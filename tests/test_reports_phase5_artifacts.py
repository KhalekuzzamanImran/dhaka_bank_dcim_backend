from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.db import IntegrityError
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
from apps.reports.services.artifacts import create_report_artifact
from apps.reports.services.definitions import get_active_definition_by_code
from apps.reports.services.factory import create_report_job
from apps.reports.services.generator import generate_report_job


@override_settings(REPORT_ARTIFACT_RETENTION_DAYS=14)
class ReportPhase5ArtifactTestCase(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="reports-artifacts-")
        self._override = override_settings(MEDIA_ROOT=self.media_root)
        self._override.enable()

        self.user = User.objects.create_user(username="artifact-user", password="test12345", is_active=True)
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
        self.definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _template(self, *, code="ARTIFACT_TEMPLATE", primary_format="CSV", attachment_formats=None):
        return ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Artifact Template",
            code=code,
            description="Artifact test template",
            config={"report_type": "device_inventory", "output_format": primary_format.lower()},
            default_parameters={},
            primary_format=primary_format,
            attachment_formats=attachment_formats or [],
            include_raw_data=False,
            is_active=True,
        )

    def _job(self, template, *, output_overrides=None):
        result = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={"report_type": "device_inventory"},
            runtime_parameters=output_overrides or {},
            queue_job=False,
        )
        return result.job

    def _file_checksum(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def test_csv_artifact_persisted_with_expected_metadata(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        generated = generate_report_job(job.id)

        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertEqual(generated.artifacts.count(), 1)
        artifact = generated.artifacts.get()
        self.assertEqual(artifact.format, "CSV")
        self.assertTrue(default_storage.exists(artifact.file.name))
        self.assertTrue(artifact.file.name.startswith(f"reports/{self.org.id}/"))
        self.assertIn(f"/{generated.id}/", artifact.file.name)
        self.assertTrue(re.search(r"/[a-z0-9_-]+_\d{4}-\d{2}-\d{2}_job-[a-f0-9]{8}\.csv$", artifact.file.name))
        self.assertEqual(artifact.content_type, "text/csv")
        self.assertGreater(artifact.size_bytes, 0)
        self.assertEqual(len(artifact.checksum_sha256), 64)
        self.assertEqual(artifact.checksum_sha256, self._file_checksum(artifact.file.path))
        self.assertEqual(artifact.size_bytes, os.path.getsize(artifact.file.path))
        self.assertEqual(artifact.retention_expires_at.date(), (timezone.now() + timedelta(days=14)).date())

    def test_multiple_formats_create_multiple_artifacts(self):
        template = self._template(primary_format="PDF", attachment_formats=["CSV"])
        job = self._job(template)
        generated = generate_report_job(job.id)

        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertEqual(generated.artifacts.count(), 2)
        self.assertEqual(set(generated.artifacts.values_list("format", flat=True)), {"PDF", "CSV"})
        self.assertTrue(all(default_storage.exists(artifact.file.name) for artifact in generated.artifacts.all()))

    def test_duplicate_processing_reuses_existing_artifact(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        first = generate_report_job(job.id)
        artifact_ids = list(first.artifacts.values_list("id", flat=True))

        second = generate_report_job(job.id)
        self.assertEqual(second.status, ReportJobStatus.COMPLETED)
        self.assertEqual(second.artifacts.count(), 1)
        self.assertEqual(list(second.artifacts.values_list("id", flat=True)), artifact_ids)

    def test_completed_job_is_not_regenerated(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        generated = generate_report_job(job.id)
        refreshed_at = generated.updated_at
        regenerated = generate_report_job(job.id)
        self.assertEqual(regenerated.id, generated.id)
        self.assertEqual(regenerated.status, ReportJobStatus.COMPLETED)
        self.assertEqual(regenerated.updated_at, refreshed_at)
        self.assertEqual(regenerated.artifacts.count(), 1)

    def test_database_failure_cleans_up_stored_file(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        with patch("apps.reports.services.artifacts.ReportArtifact.save", side_effect=IntegrityError("db failure")):
            generated = generate_report_job(job.id)
        self.assertEqual(generated.status, ReportJobStatus.FAILED)
        self.assertEqual(generated.artifacts.count(), 0)
        self.assertFalse(any(path.is_file() for path in Path(self.media_root).rglob("*")))

    def test_storage_failure_marks_job_failed(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        with patch("apps.reports.services.artifacts.default_storage.save", side_effect=OSError("storage failure")):
            generated = generate_report_job(job.id)
        self.assertEqual(generated.status, ReportJobStatus.FAILED)
        self.assertIn("storage failure", generated.error_message.lower())

    def test_temporary_files_are_cleaned_up(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        created_temp_paths = []
        real_named_temp = tempfile.NamedTemporaryFile

        def tracked_named_temp(*args, **kwargs):
            handle = real_named_temp(*args, **kwargs)
            created_temp_paths.append(handle.name)
            return handle

        with patch("apps.reports.services.execution.tempfile.NamedTemporaryFile", side_effect=tracked_named_temp):
            generated = generate_report_job(job.id)

        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        for temp_path in created_temp_paths:
            self.assertFalse(os.path.exists(temp_path))

    def test_job_is_completed_only_after_artifact_creation(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        observed = {"running_seen": False}

        def wrapped_create_report_artifact(*args, **kwargs):
            current = ReportJob.objects.get(pk=job.pk)
            if not observed["running_seen"]:
                self.assertEqual(current.status, ReportJobStatus.RUNNING)
                observed["running_seen"] = True
            result = create_report_artifact(*args, **kwargs)
            current.refresh_from_db()
            self.assertIn(current.status, [ReportJobStatus.RUNNING, ReportJobStatus.COMPLETED])
            return result

        with patch("apps.reports.services.execution.create_report_artifact", side_effect=wrapped_create_report_artifact):
            generated = generate_report_job(job.id)

        self.assertTrue(observed["running_seen"])
        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertEqual(generated.artifacts.count(), 1)

    def test_legacy_job_compatibility_remains_intact(self):
        template = self._template(primary_format="CSV")
        legacy_job = ReportJob.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            status=ReportJobStatus.PENDING,
            parameters={"report_type": "device_inventory"},
            trigger_source=ReportTriggerSource.MANUAL,
        )
        generated = generate_report_job(legacy_job.id)
        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertTrue(generated.artifacts.exists())
        self.assertEqual(generated.artifacts.count(), 1)

    def test_artifact_audit_event_is_written(self):
        template = self._template(primary_format="CSV")
        job = self._job(template)
        generated = generate_report_job(job.id)
        artifact = generated.artifacts.get()
        self.assertTrue(
            AuditLog.objects.filter(
                action="REPORT_ARTIFACT_CREATED",
                resource_type="ReportArtifact",
                resource_id=str(artifact.id),
                organization=self.org,
            ).exists()
        )
