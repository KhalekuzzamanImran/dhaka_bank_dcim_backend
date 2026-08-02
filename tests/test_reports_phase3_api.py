from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.domain import ReportDeliveryStatus, ReportJobStatus, ReportScheduleStatus
from apps.reports.models import ReportArtifact, ReportDefinition, ReportDelivery, ReportJob, ReportSchedule, ReportTemplate
from apps.reports.services.execution import ReportExecutionService
from apps.reports.services.jobs import ReportJobService


class ReportPhase3ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="admin", email="admin@example.com", password="test12345")
        self.user = User.objects.create_user(username="report-user", email="report-user@example.com", password="test12345", is_active=True)
        self.foreign_user = User.objects.create_user(username="foreign-user", email="foreign-user@example.com", password="test12345", is_active=True)

        self.role = Role.objects.create(name="Report API Role", code="REPORT_API_ROLE", scope=RoleScope.ORGANIZATION, status="ACTIVE")
        for code in [
            "report.view",
            "report.create",
            "report.update",
            "report.delete",
            "report.generate",
            "report.download",
        ]:
            perm = Permission.objects.get_or_create(code=code, defaults={"module": "report", "description": code})[0]
            RolePermission.objects.get_or_create(role=self.role, permission=perm)

        self.view_only_role = Role.objects.create(name="Report View Only", code="REPORT_VIEW_ONLY", scope=RoleScope.ORGANIZATION, status="ACTIVE")
        view_perm = Permission.objects.get_or_create(code="report.view", defaults={"module": "report", "description": "report.view"})[0]
        RolePermission.objects.get_or_create(role=self.view_only_role, permission=view_perm)

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

        UserResourceAccess.objects.create(user=self.user, role=self.role, organization=self.org, assigned_by=self.admin, is_active=True)
        UserResourceAccess.objects.create(user=self.foreign_user, role=self.role, organization=self.other_org, assigned_by=self.admin, is_active=True)
        self.view_only_user = User.objects.create_user(username="report-view-only", email="report-view-only@example.com", password="test12345", is_active=True)
        UserResourceAccess.objects.create(user=self.view_only_user, role=self.view_only_role, organization=self.org, assigned_by=self.admin, is_active=True)
        self.definition = ReportDefinition.objects.get(code="device_inventory")
        self.other_definition = ReportDefinition.objects.get(code="alert_summary")

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def _template_payload(self, *, definition_code="device_inventory", organization_id=None, code="DEVICE-INV"):
        return {
            "organization_id": str(organization_id or self.org.id),
            "definition_code": definition_code,
            "name": "Device Inventory Template",
            "code": code,
            "description": "Test template",
            "config": {"report_type": definition_code},
            "branding_config": {},
            "is_default": True,
            "is_active": True,
        }

    def _schedule_payload(self, template_id):
        return {
            "organization_id": str(self.org.id),
            "data_center_id": str(self.dc.id),
            "definition_code": "device_inventory",
            "template_id": str(template_id),
            "name": "Daily Inventory",
            "parameters": {"report_type": "device_inventory"},
            "primary_format": "CSV",
            "attachment_formats": [],
            "recurrence_rule": {"frequency": "DAILY", "delivery_time": "06:00:00"},
            "start_at": timezone.now().isoformat(),
            "status": "ACTIVE",
            "recipients": [
                {"channel": "EMAIL", "recipient_type": "TO", "destination": "ops@example.com"},
                {"channel": "SMS", "recipient_type": "TO", "destination": "01610043686"},
            ],
        }

    def _execute_job(self, template=None):
        template = template or ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="INVENTORY-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )
        job = ReportJobService.create_manual_job(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=template,
            parameters={"report_type": "device_inventory"},
            primary_format="CSV",
            attachment_formats=[],
            requested_by=self.user,
            recipients=[{"channel": "EMAIL", "recipient_type": "TO", "destination": "ops@example.com"}],
            enqueue=False,
        )
        ReportExecutionService.execute_job(job.id)
        job.refresh_from_db()
        return job

    def test_definition_list_and_parameter_schema(self):
        self._auth(self.user)
        response = self.client.get("/api/v1/reports/definitions/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item["code"] == "device_inventory" for item in response.json()["results"]))

        schema_response = self.client.get("/api/v1/reports/definitions/device_inventory/parameter-schema/")
        self.assertEqual(schema_response.status_code, 200)
        payload = schema_response.json()
        self.assertEqual(payload["code"], "device_inventory")
        self.assertIn("parameter_schema", payload)
        self.assertNotIn("handler_key", payload)

    def test_inactive_definition_hidden_from_normal_users(self):
        self.definition.is_active = False
        self.definition.save(update_fields=["is_active", "updated_at"])

        self._auth(self.user)
        response = self.client.get("/api/v1/reports/definitions/")
        self.assertEqual(response.status_code, 200)
        codes = [item["code"] for item in response.json()["results"]]
        self.assertNotIn("device_inventory", codes)

        self._auth(self.admin)
        admin_response = self.client.get("/api/v1/reports/definitions/")
        self.assertEqual(admin_response.status_code, 200)
        admin_codes = [item["code"] for item in admin_response.json()["results"]]
        self.assertIn("device_inventory", admin_codes)

    def test_template_create_and_delete_guard(self):
        self._auth(self.user)
        create_response = self.client.post("/api/v1/reports/templates/", self._template_payload(), format="json")
        self.assertEqual(create_response.status_code, 201)
        template_id = create_response.json()["id"]
        template = ReportTemplate.objects.get(pk=template_id)
        self.assertEqual(template.created_by_id, self.user.id)
        self.assertEqual(template.definition.code, "device_inventory")

        ReportJobService.create_manual_job(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=template,
            parameters={"report_type": "device_inventory"},
            primary_format="CSV",
            attachment_formats=[],
            requested_by=self.user,
            recipients=[{"channel": "EMAIL", "recipient_type": "TO", "destination": "ops@example.com"}],
            enqueue=False,
        )

        delete_response = self.client.delete(f"/api/v1/reports/templates/{template_id}/")
        self.assertEqual(delete_response.status_code, 409)
        self.assertEqual(delete_response.json()["code"], "TEMPLATE_IN_USE")

    def test_global_template_create_is_denied_for_normal_user(self):
        self._auth(self.user)
        payload = self._template_payload(organization_id=None, code="GLOBAL-TEMPLATE")
        payload.pop("organization_id")
        response = self.client.post("/api/v1/reports/templates/", payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_job_create_returns_202_and_no_snapshots(self):
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="JOB-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )

        self._auth(self.user)
        response = self.client.post(
            "/api/v1/reports/jobs/",
            {
                "organization_id": str(self.org.id),
                "data_center_id": str(self.dc.id),
                "definition_code": "device_inventory",
                "template_id": str(template.id),
                "parameters": {"report_type": "device_inventory"},
                "primary_format": "CSV",
                "attachment_formats": [],
                "recipients": [{"channel": "EMAIL", "recipient_type": "TO", "destination": "ops@example.com"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "QUEUED")
        self.assertIn("detail_url", payload)
        job = ReportJob.objects.get(pk=payload["id"])
        self.assertEqual(job.trigger_source, "API")

        detail_response = self.client.get(f"/api/v1/reports/jobs/{job.id}/")
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()
        self.assertNotIn("parameters_snapshot", detail_payload)
        self.assertNotIn("recipient_snapshot", detail_payload)

    def test_job_retry_creates_child_job(self):
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="RETRY-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )
        failed_job = ReportJobService.create_manual_job(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=template,
            parameters={"report_type": "device_inventory"},
            primary_format="CSV",
            attachment_formats=[],
            requested_by=self.user,
            recipients=[{"channel": "EMAIL", "recipient_type": "TO", "destination": "ops@example.com"}],
            enqueue=False,
        )
        failed_job.status = ReportJobStatus.FAILED
        failed_job.started_at = timezone.now()
        failed_job.completed_at = timezone.now()
        failed_job.error_message = "boom"
        failed_job.save(update_fields=["status", "started_at", "completed_at", "error_message", "updated_at"])

        self._auth(self.user)
        response = self.client.post(f"/api/v1/reports/jobs/{failed_job.id}/retry/", {}, format="json")
        self.assertEqual(response.status_code, 202)
        retry_job = ReportJob.objects.exclude(pk=failed_job.pk).filter(parent_job=failed_job).first()
        self.assertIsNotNone(retry_job)
        self.assertEqual(retry_job.retry_count, 1)

    def test_schedule_create_and_run_now_preserves_next_run(self):
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="SCHEDULE-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )
        self._auth(self.user)
        create_response = self.client.post("/api/v1/reports/schedules/", self._schedule_payload(template.id), format="json")
        self.assertEqual(create_response.status_code, 201)
        self.assertIn("allowed_actions", create_response.json())
        self.assertIn("run_now", create_response.json()["allowed_actions"])
        schedule = ReportSchedule.objects.get(pk=create_response.json()["id"])
        original_next_run = schedule.next_run_at

        detail_response = self.client.get(f"/api/v1/reports/schedules/{schedule.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("allowed_actions", detail_response.json())
        self.assertIn("run_now", detail_response.json()["allowed_actions"])

        run_now_response = self.client.post(f"/api/v1/reports/schedules/{schedule.id}/run-now/", {}, format="json")
        self.assertEqual(run_now_response.status_code, 202)
        self.assertIn("allowed_actions", run_now_response.json()["schedule"])
        self.assertIn("run_now", run_now_response.json()["schedule"]["allowed_actions"])
        run_now_payload = run_now_response.json()["job"]
        self.assertEqual(run_now_payload["trigger_source"], "RUN_NOW")
        self.assertEqual(run_now_payload["status"], "QUEUED")
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, original_next_run)
        self.assertTrue(ReportJob.objects.filter(pk=run_now_payload["id"]).exists())

    def test_schedule_list_and_pause_resume_actions_are_consistent(self):
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="ACTION-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )
        self._auth(self.user)
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            definition=self.definition,
            template=template,
            name="Action Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time="06:00:00",
            primary_format="CSV",
            attachment_formats=[],
            recurrence_rule={"frequency": "DAILY", "delivery_time": "06:00:00"},
            status=ReportScheduleStatus.ACTIVE,
            start_at=timezone.now(),
            parameters={"report_type": "device_inventory"},
            recipients=["ops@example.com"],
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now(),
        )

        list_response = self.client.get("/api/v1/reports/schedules/")
        self.assertEqual(list_response.status_code, 200)
        row = next(item for item in list_response.json()["results"] if item["id"] == str(schedule.id))
        self.assertIn("allowed_actions", row)
        self.assertIn("run_now", row["allowed_actions"])
        self.assertIn("pause", row["allowed_actions"])

        pause_response = self.client.post(f"/api/v1/reports/schedules/{schedule.id}/pause/", {}, format="json")
        self.assertEqual(pause_response.status_code, 200)
        self.assertNotIn("run_now", pause_response.json()["allowed_actions"])
        self.assertIn("resume", pause_response.json()["allowed_actions"])

        blocked_run_now = self.client.post(f"/api/v1/reports/schedules/{schedule.id}/run-now/", {}, format="json")
        self.assertEqual(blocked_run_now.status_code, 409)
        self.assertEqual(blocked_run_now.json()["code"], "SCHEDULE_NOT_RUNNABLE")

        resume_response = self.client.post(f"/api/v1/reports/schedules/{schedule.id}/resume/", {}, format="json")
        self.assertEqual(resume_response.status_code, 200)
        self.assertIn("run_now", resume_response.json()["allowed_actions"])
        self.assertIn("pause", resume_response.json()["allowed_actions"])

    def test_schedule_run_now_requires_backend_permission(self):
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory Template",
            code="VIEW-ONLY-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )
        self._auth(self.user)
        create_response = self.client.post("/api/v1/reports/schedules/", self._schedule_payload(template.id), format="json")
        schedule_id = create_response.json()["id"]

        self._auth(self.view_only_user)
        detail_response = self.client.get(f"/api/v1/reports/schedules/{schedule_id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotIn("run_now", detail_response.json()["allowed_actions"])

        run_now_response = self.client.post(f"/api/v1/reports/schedules/{schedule_id}/run-now/", {}, format="json")
        self.assertEqual(run_now_response.status_code, 403)

    def test_schedule_preview_next_runs(self):
        self._auth(self.user)
        response = self.client.post(
            "/api/v1/reports/schedules/preview-next-runs/",
            {
                "recurrence_rule": {"frequency": "DAILY", "delivery_time": "06:00:00"},
                "count": 3,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 3)

    def test_artifact_download_and_delivery_retry(self):
        job = self._execute_job()
        artifact = job.primary_artifact
        self.assertIsNotNone(artifact)

        self._auth(self.user)
        download_response = self.client.get(f"/api/v1/reports/artifacts/{artifact.id}/download/")
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", download_response["Content-Disposition"])

        failed_delivery = ReportDelivery.objects.create(
            job=job,
            schedule=None,
            artifact=artifact,
            channel="EMAIL",
            recipient_type="TO",
            destination_snapshot="ops@example.com",
            attempt_number=1,
            status=ReportDeliveryStatus.FAILED,
            error_message="smtp failed",
        )
        retry_response = self.client.post(f"/api/v1/reports/deliveries/{failed_delivery.id}/retry/", {}, format="json")
        self.assertEqual(retry_response.status_code, 202)
        self.assertTrue(ReportDelivery.objects.filter(job=job, attempt_number=2).exists())

    def test_cross_organization_access_is_denied(self):
        foreign_template = ReportTemplate.objects.create(
            organization=self.other_org,
            definition=self.definition,
            name="Foreign Template",
            code="FOREIGN-TEMPLATE",
            config={"report_type": "device_inventory"},
            branding_config={},
            is_active=True,
            created_by=self.admin,
        )
        foreign_schedule = ReportSchedule.objects.create(
            organization=self.other_org,
            data_center=self.other_dc,
            definition=self.definition,
            template=foreign_template,
            name="Foreign Schedule",
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
            created_by=self.admin,
            next_run_at=timezone.now(),
        )

        self._auth(self.user)
        response = self.client.get(f"/api/v1/reports/schedules/{foreign_schedule.id}/")
        self.assertIn(response.status_code, {403, 404})

    def test_dashboard_and_options_endpoints(self):
        self._auth(self.user)
        dashboard_response = self.client.get("/api/v1/reports/dashboard/")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn("summary", dashboard_response.json())
        self.assertIn("report_catalogue", dashboard_response.json())

        options_response = self.client.get("/api/v1/reports/options/")
        self.assertEqual(options_response.status_code, 200)
        self.assertIn("schedule_statuses", options_response.json())
        self.assertIn("artifact_types", options_response.json())
