from __future__ import annotations

import os

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.models import ReportJob, ReportJobStatus, ReportTemplate
from apps.reports.services.generator import generate_report_job


class ReportTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="report-user", password="test12345", is_active=True)
        self.other_user = User.objects.create_user(username="other-user", password="test12345", is_active=True)
        self.role = self._role("REPORT_MANAGER", "Report Manager")
        self._grant_permissions(
            self.role,
            [
                "report.view",
                "report.create",
                "report.update",
                "report.delete",
                "report.generate",
                "report.download",
            ],
        )
        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.other_org = Organization.objects.create(name="Org Two", code="ORG-2")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code="DC-2")
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
        self.other_device = Device.objects.create(
            organization=self.other_org,
            data_center=self.other_dc,
            device_type=self.device_type,
            device_model=self.device_model,
            name="UPS-02",
            code="UPS-02",
        )
        self._assign_access(self.user, organization=self.org)

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

    def _assign_access(self, user, *, organization=None, data_center=None):
        return UserResourceAccess.objects.create(
            user=user,
            role=self.role,
            organization=organization,
            data_center=data_center,
            assigned_by=self.user,
            is_active=True,
        )

    def _template(self, *, organization=None, code="DEVICE_INVENTORY", report_type="device_inventory", config=None, is_active=True):
        organization = organization or self.org
        config = config if config is not None else {"report_type": report_type, "output_format": "csv"}
        return ReportTemplate.objects.create(
            organization=organization,
            name=code.replace("_", " ").title(),
            code=code,
            description="Test template",
            config=config,
            is_active=is_active,
        )

    def _job(self, *, organization=None, data_center=None, template=None, requested_by=None, parameters=None, status=ReportJobStatus.PENDING):
        return ReportJob.objects.create(
            organization=organization or self.org,
            data_center=data_center,
            template=template,
            requested_by=requested_by or self.user,
            parameters=parameters if parameters is not None else {"report_type": (template.report_type if template else "device_inventory")},
            status=status,
            started_at=timezone.now() if status in {ReportJobStatus.PROCESSING, ReportJobStatus.COMPLETED, ReportJobStatus.FAILED, ReportJobStatus.CANCELLED} else None,
            completed_at=timezone.now() if status in {ReportJobStatus.COMPLETED, ReportJobStatus.FAILED, ReportJobStatus.CANCELLED} else None,
            error_message="error" if status == ReportJobStatus.FAILED else "",
        )

    def tearDown(self):
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

    def test_report_template_config_must_be_dict(self):
        template = ReportTemplate(
            organization=self.org,
            name="Invalid",
            code="INVALID",
            config=[],
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            template.full_clean()

    def test_duplicate_active_template_code_per_organization_is_rejected(self):
        self._template(code="DUPLICATE_TEMPLATE")
        duplicate = ReportTemplate(
            organization=self.org,
            name="Duplicate",
            code="DUPLICATE_TEMPLATE",
            config={"report_type": "device_inventory"},
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_report_job_rejects_data_center_from_different_organization(self):
        template = self._template()
        job = ReportJob(
            organization=self.org,
            data_center=self.other_dc,
            template=template,
            requested_by=self.user,
            parameters={"report_type": "device_inventory"},
        )
        with self.assertRaises(ValidationError):
            job.full_clean()

    def test_report_job_rejects_template_from_different_organization(self):
        template = self._template(organization=self.other_org, code="OTHER_TEMPLATE")
        job = ReportJob(
            organization=self.org,
            data_center=None,
            template=template,
            requested_by=self.user,
            parameters={"report_type": "device_inventory"},
        )
        with self.assertRaises(ValidationError):
            job.full_clean()

    def test_report_job_create_rejects_unauthorized_organization(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(organization=self.other_org, code="UNAUTHORIZED_TEMPLATE")
        response = self.client.post(
            "/api/v1/reports/report-jobs/",
            {
                "organization": str(self.other_org.id),
                "data_center": str(self.other_dc.id),
                "template": str(template.id),
                "parameters": {"report_type": "device_inventory"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_report_job_create_sets_requested_by_from_request_user(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="CREATE_TEMPLATE")
        response = self.client.post(
            "/api/v1/reports/report-jobs/",
            {
                "organization": str(self.org.id),
                "data_center": str(self.dc.id),
                "template": str(template.id),
                "parameters": {"report_type": "device_inventory"},
                "status": ReportJobStatus.COMPLETED,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], ReportJobStatus.PENDING)
        self.assertEqual(response.json()["requested_by"], str(self.user.id))

    def test_normal_api_cannot_directly_set_completed_status(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="STATUS_TEMPLATE")
        response = self.client.post(
            "/api/v1/reports/report-jobs/",
            {
                "organization": str(self.org.id),
                "template": str(template.id),
                "parameters": {"report_type": "device_inventory"},
                "status": ReportJobStatus.COMPLETED,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], ReportJobStatus.PENDING)

    def test_generate_action_enqueues_and_can_run_generation(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="GENERATE_TEMPLATE")
        job = self._job(template=template, parameters={"report_type": "device_inventory"})

        with patch("apps.reports.tasks.generate_report_job_task.delay", side_effect=lambda job_id: generate_report_job(job_id)):
            response = self.client.post(f"/api/v1/reports/report-jobs/{job.id}/generate/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, ReportJobStatus.COMPLETED)
        self.assertTrue(job.file)
        self.assertTrue(os.path.exists(job.file.path))

    def test_successful_generation_marks_completed_and_creates_file(self):
        template = self._template(code="SUCCESS_TEMPLATE")
        job = self._job(template=template, parameters={"report_type": "device_inventory"})
        generated = generate_report_job(job.id)
        generated.refresh_from_db()
        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertTrue(generated.file)
        self.assertTrue(os.path.exists(generated.file.path))
        with generated.file.open("r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("device_id", content)
        self.assertIn("UPS-01", content)

    def test_failed_generation_marks_failed_and_stores_error_message(self):
        template = self._template(code="BROKEN_TEMPLATE", config={"report_type": "unknown_type", "output_format": "csv"})
        job = self._job(template=template, parameters={"report_type": "unknown_type"})
        generated = generate_report_job(job.id)
        generated.refresh_from_db()
        self.assertEqual(generated.status, ReportJobStatus.FAILED)
        self.assertIn("Unsupported report type", generated.error_message)

    def test_retry_works_only_for_failed_jobs(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="RETRY_TEMPLATE")
        job = self._job(template=template, parameters={"report_type": "device_inventory"}, status=ReportJobStatus.FAILED)
        job.error_message = "Temporary failure"
        job.save(update_fields=["status", "started_at", "completed_at", "error_message", "updated_at"])

        with patch("apps.reports.tasks.generate_report_job_task.delay", side_effect=lambda job_id: generate_report_job(job_id)):
            response = self.client.post(f"/api/v1/reports/report-jobs/{job.id}/retry/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, ReportJobStatus.COMPLETED)

    def test_download_works_only_for_completed_jobs_with_file(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="DOWNLOAD_TEMPLATE")
        job = self._job(template=template, parameters={"report_type": "device_inventory"})
        generate_report_job(job.id)

        response = self.client.get(f"/api/v1/reports/report-jobs/{job.id}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"].lower())

    def test_user_cannot_see_or_download_another_scope_report(self):
        self.client.force_authenticate(user=self.user)
        other_template = self._template(organization=self.other_org, code="OTHER_ORG_TEMPLATE")
        other_job = self._job(organization=self.other_org, data_center=self.other_dc, template=other_template, requested_by=self.other_user)
        generate_report_job(other_job.id)

        list_response = self.client.get("/api/v1/reports/report-jobs/")
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        results = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        self.assertFalse(any(row["id"] == str(other_job.id) for row in results))

        download_response = self.client.get(f"/api/v1/reports/report-jobs/{other_job.id}/download/")
        self.assertEqual(download_response.status_code, 404)

    def test_empty_dataset_still_generates_valid_report(self):
        empty_org = Organization.objects.create(name="Empty Org", code="ORG-EMPTY")
        template = self._template(organization=empty_org, code="EMPTY_TEMPLATE")
        job = self._job(organization=empty_org, template=template, parameters={"report_type": "device_inventory"})
        generated = generate_report_job(job.id)
        with generated.file.open("r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("device_id", content)
        self.assertNotIn("UPS-01", content)

    def test_report_output_respects_organization_and_data_center_scope(self):
        template = self._template(code="SCOPE_TEMPLATE")
        Device.objects.create(
            organization=self.org,
            data_center=self.other_dc,
            device_type=self.device_type,
            device_model=self.device_model,
            name="UPS-OTHER-DC",
            code="UPS-OTHER-DC",
        )
        job = self._job(template=template, data_center=self.dc, parameters={"report_type": "device_inventory"})
        generated = generate_report_job(job.id)
        with generated.file.open("r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("UPS-01", content)
        self.assertNotIn("UPS-OTHER-DC", content)
