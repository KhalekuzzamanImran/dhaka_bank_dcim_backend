from __future__ import annotations

import shutil
import tempfile
import uuid

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.enums import ReportTriggerSource
from apps.reports.models import ReportDefinition, ReportJobStatus, ReportTemplate
from apps.reports.services.factory import create_report_job
from apps.reports.services.execution import generate_report_job


@override_settings(REPORT_ARTIFACT_RETENTION_DAYS=14)
class ReportPhase5BDownloadTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_root = tempfile.mkdtemp(prefix="reports-downloads-")
        self._override = override_settings(MEDIA_ROOT=self.media_root)
        self._override.enable()

        suffix = str(uuid.uuid4())[:8]
        self.user = User.objects.create_user(username=f"download-user-{suffix}", password="test12345", is_active=True)
        self.other_user = User.objects.create_user(username=f"other-user-{suffix}", password="test12345", is_active=True)
        self.role = self._role(f"REPORT_ROLE_{suffix}", "Report Role")
        self._grant_permissions(self.role, ["report.view", "report.download"])
        self.org = Organization.objects.create(name=f"Org {suffix}", code=f"ORG-{suffix}")
        self.dc = DataCenter.objects.create(organization=self.org, name=f"DC {suffix}", code=f"DC-{suffix}")
        self.other_org = Organization.objects.create(name=f"Other Org {suffix}", code=f"ORG-OTHER-{suffix}")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name=f"Other DC {suffix}", code=f"DC-OTHER-{suffix}")
        self.device_type = DeviceType.objects.create(name=f"UPS {suffix}", code=f"UPS{suffix.upper()}", category="POWER")
        self.vendor = Vendor.objects.create(name=f"Vendor {suffix}", code=f"VEN{suffix.upper()}")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name=f"Model {suffix}",
            model_number=f"M1-{suffix}",
        )
        Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            device_type=self.device_type,
            device_model=self.device_model,
            name=f"UPS-{suffix}",
            code=f"UPS-{suffix}",
        )
        self._assign_access(self.user, organization=self.org)
        self._assign_access(self.other_user, organization=self.other_org)
        self.definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _template(self):
        return ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Download Template",
            code=f"DOWNLOAD-{uuid.uuid4().hex[:8]}",
            description="Download test template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={},
            primary_format="CSV",
            attachment_formats=[],
            include_raw_data=False,
            is_active=True,
        )

    def _perm(self, code):
        return Permission.objects.get_or_create(
            code=code,
            defaults={"module": code.split(".")[0], "description": code},
        )[0]

    def _role(self, code, name):
        return Role.objects.update_or_create(
            code=code,
            defaults={"name": name, "scope": RoleScope.ORGANIZATION, "status": "ACTIVE"},
        )[0]

    def _grant_permissions(self, role, codes):
        for code in codes:
            RolePermission.objects.get_or_create(role=role, permission=self._perm(code))

    def _assign_access(self, user, *, organization):
        UserResourceAccess.objects.create(
            user=user,
            role=self.role,
            organization=organization,
            assigned_by=self.user if user != self.user else None,
            is_active=True,
        )

    def _generated_job(self):
        template = self._template()
        result = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={},
            queue_job=False,
        )
        job = result.job
        generated = generate_report_job(job.id)
        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        artifact = generated.artifacts.get()
        return generated, artifact

    def test_artifact_download_returns_content_and_audit_event(self):
        generated, artifact = self._generated_job()
        self.client.force_authenticate(user=self.user)

        response = self.client.get(f"/api/v1/reports/artifacts/{artifact.id}/download/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(artifact.original_filename, response["Content-Disposition"])
        self.assertEqual(int(response["Content-Length"]), artifact.size_bytes)
        self.assertTrue(default_storage.exists(artifact.file.name))
        self.assertTrue(
            AuditLog.objects.filter(
                action="REPORT_ARTIFACT_DOWNLOADED",
                resource_type="ReportArtifact",
                resource_id=str(artifact.id),
                organization=self.org,
            ).exists()
        )

    def test_artifact_download_supports_range_requests(self):
        _, artifact = self._generated_job()
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            f"/api/v1/reports/artifacts/{artifact.id}/download/",
            HTTP_RANGE="bytes=0-9",
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertIn("bytes 0-9/", response["Content-Range"])
        self.assertEqual(len(response.content), 10)

    def test_artifact_download_rejects_unauthorized_access(self):
        _, artifact = self._generated_job()
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(f"/api/v1/reports/report-artifacts/{artifact.id}/download/")

        self.assertEqual(response.status_code, 404)

    def test_job_download_uses_artifact_authoritatively(self):
        generated, artifact = self._generated_job()
        self.client.force_authenticate(user=self.user)

        response = self.client.get(f"/api/v1/reports/artifacts/{artifact.id}/download/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(artifact.original_filename, response["Content-Disposition"])
        self.assertTrue(
            AuditLog.objects.filter(
                action="REPORT_ARTIFACT_DOWNLOADED",
                resource_type="ReportArtifact",
                resource_id=str(artifact.id),
                organization=self.org,
            ).exists()
        )
