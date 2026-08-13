from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.notifications.models import NotificationChannel
from apps.organizations.models import Organization
from apps.reports.enums import ReportDeliveryStatus, ReportScheduleStatus, ReportTriggerSource
from apps.reports.models import (
    ReportArtifact,
    ReportDefinition,
    ReportDelivery,
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportScheduleStatus as LegacyScheduleStatus,
    ReportTemplate,
)
from apps.reports.services.definitions import seed_report_definitions
from apps.reports.services.factory import create_report_job
from apps.reports.services.execution import generate_report_job
from apps.reports.services.schedules import execute_report_schedule


def _perm(code):
    return Permission.objects.get_or_create(code=code, defaults={"module": code.split(".")[0], "description": code})[0]


def _role(code, name, scope, perm_codes):
    role, _ = Role.objects.update_or_create(code=code, defaults={"name": name, "scope": scope, "status": "ACTIVE"})
    for perm_code in perm_codes:
        RolePermission.objects.get_or_create(role=role, permission=_perm(perm_code))
    return role


@override_settings(REPORT_ARTIFACT_RETENTION_DAYS=14)
class ReportPhase6CApiTestCase(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="reports-api-")
        self._media_override = override_settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        seed_report_definitions()
        self.client = APIClient()
        self.user = User.objects.create_user(username=f"report-user-{uuid4().hex[:8]}", password="test12345", is_active=True)
        self.other_user = User.objects.create_user(username=f"other-user-{uuid4().hex[:8]}", password="test12345", is_active=True)
        self.role = _role(
            "REPORT_API",
            "Report API",
            RoleScope.ORGANIZATION,
            ["report.view", "report.create", "report.update", "report.delete", "report.generate", "report.download"],
        )
        self.org = Organization.objects.create(name="Org One", code=f"ORG-{uuid4().hex[:6].upper()}")
        self.other_org = Organization.objects.create(name="Org Two", code=f"ORG-{uuid4().hex[:6].upper()}")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code=f"DC-{uuid4().hex[:6].upper()}")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code=f"DC-{uuid4().hex[:6].upper()}")
        UserResourceAccess.objects.create(user=self.user, role=self.role, organization=self.org, assigned_by=self.user, is_active=True)
        self.definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _auth(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def _template_payload(self, **overrides):
        payload = {
            "organization": str(self.org.id),
            "name": "Inventory Template",
            "code": "TPL-INVENTORY",
            "description": "Normalized API template",
            "definition": "DEVICE_INVENTORY",
            "configuration": {"report_type": "device_inventory", "output_format": "csv"},
            "default_parameters": {"report_type": "device_inventory"},
            "primary_format": "CSV",
            "attachment_formats": [],
            "include_charts": False,
            "include_raw_data": False,
            "is_active": True,
        }
        payload.update(overrides)
        return payload

    def _schedule_payload(self, template_id, **overrides):
        payload = {
            "organization": str(self.org.id),
            "data_center": str(self.dc.id),
            "template": str(template_id),
            "name": "Daily Inventory",
            "status": "ACTIVE",
            "frequency": "DAILY",
            "timezone": "Asia/Dhaka",
            "delivery_time": "06:00",
            "days_of_week": [],
            "day_of_month": None,
            "parameter_overrides": {"report_type": "device_inventory"},
            "recipients": [
                {"channel": "EMAIL", "display_name": "Ops", "email_address": "ops@example.com", "is_active": True},
            ],
            "sms_recipients": [
                {"channel": "SMS", "display_name": "Ops SMS", "phone_number": "01329665857", "is_active": True},
            ],
            "primary_format": "CSV",
            "attachment_formats": [],
        }
        payload.update(overrides)
        return payload

    def _create_completed_job(self, template):
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
        with override_settings(MEDIA_ROOT=getattr(self, "media_root", None) or "/tmp"):
            with patch("apps.reports.services.execution._queue_report_deliveries", lambda job_id: None):
                generated = generate_report_job(result.job.id)
        return generated

    def test_definitions_and_parameter_schema_are_exposed(self):
        self._auth()
        response = self.client.get("/api/v1/reports/definitions/")
        self.assertEqual(response.status_code, 200)
        definition_rows = response.json().get("results", response.json())
        self.assertTrue(any(item["code"] == "DEVICE_INVENTORY" for item in definition_rows))

        inactive = ReportDefinition.objects.create(
            code=f"INACTIVE-{uuid4().hex[:8]}",
            name="Inactive",
            description="Inactive",
            category="INVENTORY",
            generator_key=f"inactive-{uuid4().hex[:8]}",
            parameter_schema={},
            supported_formats=["CSV"],
            supported_delivery_channels=["EMAIL"],
            requires_telemetry=False,
            requires_data_center=False,
            is_system=False,
            is_active=False,
            version=1,
        )
        detail = self.client.get(f"/api/v1/reports/definitions/{inactive.code}/")
        self.assertEqual(detail.status_code, 404)

        schema = self.client.get("/api/v1/reports/definitions/DEVICE_INVENTORY/parameter-schema/")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("parameter_schema", schema.json())

    def test_template_create_update_generate_and_disable(self):
        self._auth()
        response = self.client.post("/api/v1/reports/templates/", self._template_payload(), format="json")
        self.assertEqual(response.status_code, 201)
        template_id = response.json()["id"]
        self.assertIn("configuration", response.json())
        self.assertIn("allowed_actions", response.json())

        patch_response = self.client.patch(
            f"/api/v1/reports/templates/{template_id}/",
            {"name": "Inventory Template Updated"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertGreaterEqual(patch_response.json()["version"], response.json()["version"])

        generate_response = self.client.post(f"/api/v1/reports/templates/{template_id}/generate/", {}, format="json")
        self.assertIn(generate_response.status_code, [200, 201])
        self.assertIn("delivery_summary", generate_response.json())

        delete_response = self.client.delete(f"/api/v1/reports/templates/{template_id}/")
        self.assertIn(delete_response.status_code, [204, 200])

    def test_schedule_create_run_and_history_endpoints(self):
        self._auth()
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="TPL-SCHEDULE",
            description="Normalized schedule template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={"report_type": "device_inventory"},
            primary_format="PDF",
            attachment_formats=["CSV"],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )

        create_response = self.client.post("/api/v1/reports/schedules/", self._schedule_payload(template.id), format="json")
        self.assertEqual(create_response.status_code, 201)
        payload = create_response.json()
        self.assertEqual(payload["status"], "ACTIVE")
        self.assertNotIn("is_active", payload)
        schedule_id = payload["id"]

        update_response = self.client.patch(f"/api/v1/reports/schedules/{schedule_id}/", {"status": "PAUSED"}, format="json")
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["status"], "PAUSED")

        resume_response = self.client.post(f"/api/v1/reports/schedules/{schedule_id}/resume/", {}, format="json")
        self.assertEqual(resume_response.status_code, 200)
        self.assertEqual(resume_response.json()["status"], "ACTIVE")

        with self.captureOnCommitCallbacks(execute=True):
            with patch("apps.reports.tasks.deliver_report_schedule_task.delay") as mocked_delay:
                run_now = self.client.post(f"/api/v1/reports/schedules/{schedule_id}/run-now/", {}, format="json")
                self.assertIn(run_now.status_code, [200, 202])

        runs = self.client.get(f"/api/v1/reports/schedules/{schedule_id}/runs/")
        self.assertEqual(runs.status_code, 200)
        self.assertIsInstance(runs.json(), list)

        deliveries = self.client.get(f"/api/v1/reports/schedules/{schedule_id}/deliveries/")
        self.assertEqual(deliveries.status_code, 200)
        self.assertIsInstance(deliveries.json(), list)

        preview = self.client.get(f"/api/v1/reports/schedules/{schedule_id}/preview-next-runs/")
        self.assertEqual(preview.status_code, 200)
        self.assertIn("results", preview.json())

    def test_jobs_artifacts_and_deliveries_are_normalized(self):
        self._auth()
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Job Template",
            code="TPL-JOB",
            description="Job template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={"report_type": "device_inventory"},
            primary_format="CSV",
            attachment_formats=[],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )
        job = self._create_completed_job(template)
        list_response = self.client.get("/api/v1/reports/jobs/")
        self.assertEqual(list_response.status_code, 200)
        job_rows = list_response.json().get("results", list_response.json())
        self.assertTrue(any(item["id"] == str(job.id) for item in job_rows))
        self.assertNotIn("parameters_snapshot", job_rows[0])

        detail_response = self.client.get(f"/api/v1/reports/jobs/{job.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("parameters_snapshot", detail_response.json())
        self.assertIn("artifacts", detail_response.json())

        patch_response = self.client.patch(f"/api/v1/reports/jobs/{job.id}/", {"status": "FAILED"}, format="json")
        self.assertEqual(patch_response.status_code, 405)

        artifact = ReportArtifact.objects.filter(job=job).first()
        self.assertIsNotNone(artifact)
        artifact_response = self.client.get("/api/v1/reports/artifacts/")
        self.assertEqual(artifact_response.status_code, 200)
        artifact_rows = artifact_response.json().get("results", artifact_response.json())
        self.assertTrue(any(item["id"] == str(artifact.id) for item in artifact_rows))
        download = self.client.get(f"/api/v1/reports/artifacts/{artifact.id}/download/")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download["Content-Disposition"])

        delivery = ReportDelivery.objects.create(
            job=job,
            channel=NotificationChannel.EMAIL,
            recipient="ops@example.com",
            status=ReportDeliveryStatus.FAILED,
            queued_at=timezone.now(),
            started_at=timezone.now(),
            failed_at=timezone.now(),
            error_code="TEMP",
            error_message="temporary failure",
        )
        delivery_response = self.client.get("/api/v1/reports/deliveries/")
        self.assertEqual(delivery_response.status_code, 200)
        delivery_rows = delivery_response.json().get("results", delivery_response.json())
        self.assertTrue(any(item["id"] == str(delivery.id) for item in delivery_rows))
        self.assertTrue(all("provider_response" not in item for item in delivery_rows))

    def test_options_and_legacy_routes_remain_available(self):
        self._auth()
        options = self.client.get("/api/v1/reports/options/")
        self.assertEqual(options.status_code, 200)
        self.assertIn("definitions", options.json())
        self.assertIn("supported_formats", options.json())

        canonical_templates = self.client.get("/api/v1/reports/templates/")
        self.assertEqual(canonical_templates.status_code, 200)
        legacy_templates = self.client.get("/api/v1/reports/report-templates/")
        self.assertEqual(legacy_templates.status_code, 404)

        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Legacy Schedule Template",
            code="TPL-LEGACY",
            description="Legacy",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={"report_type": "device_inventory"},
            primary_format="CSV",
            attachment_formats=[],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=template,
            name="Legacy Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameter_overrides={"report_type": "device_inventory"},
            recipients=["ops@example.com"],
            send_sms=False,
            sms_recipients=[],
            primary_format="CSV",
            attachment_formats=[],
            status=LegacyScheduleStatus.ACTIVE,
            is_active=True,
            created_by=self.user,
            updated_by=self.user,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )
        ReportScheduleRecipient.objects.create(
            schedule=schedule,
            channel="EMAIL",
            recipient_type="EMAIL",
            destination="ops@example.com",
            email_address="ops@example.com",
            is_active=True,
        )

        with self.captureOnCommitCallbacks(execute=True):
            with patch("apps.reports.tasks.deliver_report_schedule_task.delay") as mocked_delay:
                legacy_run_now = self.client.post(f"/api/v1/reports/schedules/{schedule.id}/run-now/", {}, format="json")
                self.assertIn(legacy_run_now.status_code, [200, 202])
