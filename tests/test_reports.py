from __future__ import annotations

from datetime import time, timedelta
import os

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.datacenters.models import DataCenter
from apps.datacenters.models import Rack
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.organizations.models import Organization
from apps.reports.models import ReportJob, ReportJobStatus, ReportSchedule, ReportTemplate
from apps.reports.services.generator import generate_report_job
from apps.reports.services.schedules import execute_report_schedule
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition, TelemetryPoint
from apps.datacenters.models import Room


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

    def test_report_schedule_create_accepts_ui_labels_and_sets_next_run(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/v1/reports/report-schedules/",
            {
                "organization": str(self.org.id),
                "data_center": str(self.dc.id),
                "name": "Environmental Trends Report",
                "report_type": "Environmental Trends Report",
                "frequency": "Daily",
                "delivery_time": "06:00:00",
                "output_format": "PDF / CSV",
                "recipients": ["omar@adn", "noc@adn", "facilities@adn"],
                "attach_raw_data": True,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["report_type"], "room_environment")
        self.assertEqual(payload["frequency"], "DAILY")
        self.assertEqual(payload["output_format"], "PDF_CSV")
        self.assertEqual(payload["created_by"], str(self.user.id))
        self.assertIsNotNone(payload["next_run_at"])

    def test_report_schedule_create_accepts_ampm_delivery_time(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/v1/reports/report-schedules/",
            {
                "organization": str(self.org.id),
                "data_center": str(self.dc.id),
                "name": "Environmental Trends Report",
                "report_type": "Environmental Trends Report",
                "frequency": "Daily",
                "delivery_time": "06:00 AM",
                "output_format": "PDF / CSV",
                "recipients": ["omar@adn", "noc@adn"],
                "attach_raw_data": True,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["delivery_time"], "06:00:00")

    def test_report_schedule_time_update_recalculates_next_run(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Editable Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            recipients=["admin@example.com"],
            is_active=True,
            created_by=self.user,
        )
        previous_next_run = schedule.next_run_at

        schedule.delivery_time = time(23, 59)
        schedule.save(update_fields={"delivery_time"})
        schedule.refresh_from_db()

        self.assertNotEqual(schedule.next_run_at, previous_next_run)
        self.assertEqual(schedule.next_run_at.astimezone(timezone.get_current_timezone()).time().replace(second=0, microsecond=0), time(23, 59))

    def test_generate_action_enqueues_and_can_run_generation(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="GENERATE_TEMPLATE")
        job = self._job(template=template, parameters={"report_type": "device_inventory"})

        with patch("apps.reports.tasks.generate_report_job_task.delay", side_effect=lambda job_id: generate_report_job(job_id)):
            with self.captureOnCommitCallbacks(execute=True):
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
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("device_id", content)
        self.assertIn("UPS-01", content)

    def test_failed_generation_marks_failed_and_stores_error_message(self):
        template = self._template(code="BROKEN_TEMPLATE", config={"report_type": "unknown_type", "output_format": "csv"})
        job = self._job(template=template, parameters={"report_type": "unknown_type"})
        generated = generate_report_job(job.id)
        generated.refresh_from_db()
        self.assertEqual(generated.status, ReportJobStatus.FAILED)
        self.assertIn("Unsupported report type", generated.error_message)

    def test_report_schedule_execution_generates_job_and_emails_recipients(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Environmental Trends Report",
            report_type="room_environment",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="PDF_CSV",
            recipients=["omar@adn", "noc@adn", "facilities@adn"],
            attach_raw_data=True,
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )

        with patch("apps.reports.services.schedules.EmailMessage.send", return_value=1) as mocked_send:
            executed = execute_report_schedule(str(schedule.id))

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_delivery_status, "SENT")
        self.assertIsNotNone(schedule.last_job)
        self.assertEqual(schedule.last_job.status, ReportJobStatus.COMPLETED)
        self.assertTrue(schedule.last_job.file)
        self.assertTrue(mocked_send.called)
        self.assertEqual(executed.pk, schedule.pk)

    def test_report_schedule_allows_sms_only_recipient(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="SMS-only Report",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            recipients=[],
            send_sms=True,
            sms_recipients=["01329665857"],
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )

        self.assertEqual(schedule.recipients, [])
        self.assertEqual(schedule.sms_recipients, ["01329665857"])

    def test_report_schedule_rejects_run_without_delivery_channel(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Undeliverable Report",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            recipients=["admin@example.com"],
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )
        ReportSchedule.objects.filter(pk=schedule.pk).update(recipients=[])

        with self.assertRaisesMessage(ValueError, "no email or SMS recipients"):
            execute_report_schedule(str(schedule.pk))

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_delivery_status, "FAILED")
        self.assertIn("no email or SMS recipients", schedule.last_error_message)

    def test_run_now_action_executes_schedule_and_updates_status(self):
        self.client.force_authenticate(user=self.user)
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Power Consumption Report",
            report_type="device_inventory",
            frequency="WEEKLY",
            delivery_time=time(6, 0),
            output_format="PDF_CSV",
            recipients=["diginetworkspace@gmail.com"],
            attach_raw_data=True,
            is_active=True,
            created_by=self.user,
            next_run_at=timezone.now() - timedelta(minutes=5),
        )

        with patch("apps.reports.services.schedules.EmailMessage.send", return_value=1) as mocked_send:
            response = self.client.post(f"/api/v1/reports/report-schedules/{schedule.id}/run_now/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_delivery_status, "SENT")
        self.assertIsNotNone(schedule.last_job)
        self.assertEqual(schedule.last_job.status, ReportJobStatus.COMPLETED)
        self.assertTrue(schedule.last_job.file)
        self.assertTrue(mocked_send.called)

    def test_retry_works_only_for_failed_jobs(self):
        self.client.force_authenticate(user=self.user)
        template = self._template(code="RETRY_TEMPLATE")
        job = self._job(template=template, parameters={"report_type": "device_inventory"}, status=ReportJobStatus.FAILED)
        job.error_message = "Temporary failure"
        job.save(update_fields=["status", "started_at", "completed_at", "error_message", "updated_at"])

        with patch("apps.reports.tasks.generate_report_job_task.delay", side_effect=lambda job_id: generate_report_job(job_id)):
            with self.captureOnCommitCallbacks(execute=True):
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
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
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
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("UPS-01", content)
        self.assertNotIn("UPS-OTHER-DC", content)

    def test_alert_summary_report_respects_date_range(self):
        template = self._template(code="ALERT_SUMMARY_TEMPLATE", report_type="alert_summary")
        now = timezone.now()
        old_alert = AlertEvent.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=None,
            alert_rule=None,
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.OPEN,
            message="Old alert",
            triggered_at=now - timedelta(days=3),
        )
        recent_alert = AlertEvent.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=None,
            alert_rule=None,
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.OPEN,
            message="Recent alert",
            triggered_at=now - timedelta(hours=1),
        )
        job = self._job(
            template=template,
            parameters={
                "report_type": "alert_summary",
                "date_from": (now - timedelta(hours=2)).isoformat(),
                "date_to": (now + timedelta(hours=2)).isoformat(),
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("summary,open_total,1", content)
        self.assertIn("summary,critical_open,1", content)
        self.assertNotIn("summary,open_total,2", content)

    def test_notification_delivery_report_respects_date_range(self):
        template = self._template(code="NOTIF_TEMPLATE", report_type="notification_delivery")
        now = timezone.now()
        Notification.objects.create(
            organization=self.org,
            recipient=self.user,
            channel=NotificationChannel.WEB,
            subject="Old notification",
            message="Old message",
            status=NotificationStatus.SENT,
        )
        old_notification = Notification.objects.latest("created_at")
        Notification.objects.filter(pk=old_notification.pk).update(created_at=now - timedelta(days=5))

        Notification.objects.create(
            organization=self.org,
            recipient=self.user,
            channel=NotificationChannel.EMAIL,
            subject="Recent notification",
            message="Recent message",
            status=NotificationStatus.SENT,
        )
        recent_notification = Notification.objects.latest("created_at")
        Notification.objects.filter(pk=recent_notification.pk).update(created_at=now - timedelta(hours=2))

        job = self._job(
            template=template,
            parameters={
                "report_type": "notification_delivery",
                "start_date": (now - timedelta(hours=3)).isoformat(),
                "end_date": (now + timedelta(hours=3)).isoformat(),
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("summary,total,1", content)
        self.assertIn("summary,sent,1", content)
        self.assertNotIn("summary,total,2", content)

    def test_room_environment_report_generates_room_readings(self):
        template = self._template(
            code="ROOM_ENV_TEMPLATE",
            report_type="room_environment",
            config={"report_type": "room_environment", "output_format": "csv", "metrics": ["room_temperature", "room_humidity"]},
        )
        room = Room.objects.create(data_center=self.dc, name="Server Room 1", code="SR-1")
        self.device.room = room
        self.device.save(update_fields=["room"])

        temp_metric = MetricDefinition.objects.create(
            code="room_temperature",
            name="Room Temperature",
            category=MetricCategory.ENVIRONMENT,
            data_type=MetricDataType.FLOAT,
            unit="°C",
        )
        humidity_metric = MetricDefinition.objects.create(
            code="room_humidity",
            name="Room Humidity",
            category=MetricCategory.ENVIRONMENT,
            data_type=MetricDataType.FLOAT,
            unit="%",
        )

        now = timezone.now()
        TelemetryPoint.objects.create(
            time=now - timedelta(hours=3),
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=temp_metric,
            value_float=24.6,
            raw_value_text="24.6",
            quality="GOOD",
        )
        TelemetryPoint.objects.create(
            time=now - timedelta(hours=1),
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=humidity_metric,
            value_float=51.2,
            raw_value_text="51.2",
            quality="GOOD",
        )
        TelemetryPoint.objects.create(
            time=now - timedelta(days=2),
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=temp_metric,
            value_float=22.1,
            raw_value_text="22.1",
            quality="GOOD",
        )

        job = self._job(
            template=template,
            parameters={
                "report_type": "room_environment",
                "metrics": ["room_temperature", "room_humidity"],
                "date_from": (now - timedelta(hours=4)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("room_name,room_code,device_name,device_code,metric_code", content)
        self.assertIn("Server Room 1", content)
        self.assertIn("room_temperature", content)
        self.assertIn("room_humidity", content)
        self.assertIn("24.6", content)
        self.assertIn("51.2", content)
        self.assertNotIn("22.1", content)

    def test_telemetry_export_report_generates_historical_rows_and_respects_scope(self):
        template = self._template(
            code="TELEMETRY_EXPORT_TEMPLATE",
            report_type="telemetry_export",
            config={
                "report_type": "telemetry_export",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": ["date_from", "date_to", "metric_codes"],
                "optional_filters": ["device_id", "device_model_id", "device_type_id", "data_center_id", "room_id", "rack_id"],
                "default_columns": [
                    "timestamp",
                    "organization",
                    "data_center",
                    "room",
                    "rack",
                    "device",
                    "device_model",
                    "device_type",
                    "metric_code",
                    "metric_name",
                    "value",
                    "unit",
                    "quality",
                ],
                "max_date_range_days": 31,
            },
        )
        room = Room.objects.create(data_center=self.dc, name="Telemetry Room", code="TR-1")
        rack = Rack.objects.create(data_center=self.dc, room=room, name="Rack 1", code="RK-1")
        self.device.room = room
        self.device.rack = rack
        self.device.save(update_fields=["room", "rack"])

        temp_metric = MetricDefinition.objects.create(
            code="roomTemp",
            name="Room Temperature",
            category=MetricCategory.ENVIRONMENT,
            data_type=MetricDataType.FLOAT,
            unit="C",
        )
        humidity_metric = MetricDefinition.objects.create(
            code="roomRH",
            name="Room Humidity",
            category=MetricCategory.ENVIRONMENT,
            data_type=MetricDataType.FLOAT,
            unit="%",
        )
        other_metric = MetricDefinition.objects.create(
            code="otherMetric",
            name="Other Metric",
            category=MetricCategory.OTHER,
            data_type=MetricDataType.FLOAT,
            unit="",
        )

        now = timezone.now()
        TelemetryPoint.objects.create(
            time=now - timedelta(hours=3),
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=temp_metric,
            value_float=18.1,
            raw_value_text="18.1",
            quality="GOOD",
        )
        TelemetryPoint.objects.create(
            time=now - timedelta(hours=2),
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=humidity_metric,
            value_float=51.6,
            raw_value_text="51.6",
            quality="GOOD",
        )
        TelemetryPoint.objects.create(
            time=now - timedelta(hours=1),
            organization=self.other_org,
            data_center=self.other_dc,
            device=self.other_device,
            metric=other_metric,
            value_float=99.9,
            raw_value_text="99.9",
            quality="GOOD",
        )

        job = self._job(
            template=template,
            parameters={
                "report_type": "telemetry_export",
                "date_from": (now - timedelta(hours=4)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
                "device_id": str(self.device.id),
                "metric_codes": "roomTemp,roomRH",
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")

        self.assertIn("timestamp,organization,data_center,room,rack,device,device_model,device_type,metric_code,metric_name,value,unit,quality", content)
        self.assertIn("Telemetry Room", content)
        self.assertIn("roomTemp", content)
        self.assertIn("roomRH", content)
        self.assertIn("18.1", content)
        self.assertIn("51.6", content)
        self.assertNotIn("99.9", content)

    def test_telemetry_export_accepts_legacy_environment_metric_alias(self):
        template = self._template(
            code="TELEMETRY_EXPORT_ALIAS",
            report_type="telemetry_export",
            config={"report_type": "telemetry_export", "output_format": "csv"},
        )
        metric = MetricDefinition.objects.create(
            code="pac_room_temperature",
            name="PAC Room Temperature",
            category=MetricCategory.ENVIRONMENT,
            data_type=MetricDataType.FLOAT,
            unit="C",
        )
        now = timezone.now()
        TelemetryPoint.objects.create(
            time=now - timedelta(minutes=5),
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=metric,
            value_float=18.1,
            quality="GOOD",
        )
        job = self._job(
            template=template,
            parameters={
                "report_type": "telemetry_export",
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
                "metric_codes": ["room_temperature"],
            },
        )

        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")

        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertIn("pac_room_temperature", content)
        self.assertIn("18.1", content)

    def test_telemetry_export_requires_required_filters_and_rejects_invalid_values(self):
        template = self._template(
            code="TELEMETRY_EXPORT_VALIDATION",
            report_type="telemetry_export",
            config={
                "report_type": "telemetry_export",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": ["date_from", "date_to", "metric_codes"],
                "optional_filters": ["device_id", "device_model_id", "device_type_id", "data_center_id", "room_id", "rack_id"],
                "default_columns": [
                    "timestamp",
                    "organization",
                    "data_center",
                    "room",
                    "rack",
                    "device",
                    "device_model",
                    "device_type",
                    "metric_code",
                    "metric_name",
                    "value",
                    "unit",
                    "quality",
                ],
                "max_date_range_days": 31,
            },
        )

        missing_required_job = self._job(
            template=template,
            parameters={
                "report_type": "telemetry_export",
                "metric_codes": ["roomTemp"],
            },
        )
        generated_missing_required = generate_report_job(missing_required_job.id)
        self.assertEqual(generated_missing_required.status, ReportJobStatus.FAILED)
        self.assertIn("date_from and date_to are required", generated_missing_required.error_message)

        invalid_metric_job = self._job(
            template=template,
            parameters={
                "report_type": "telemetry_export",
                "date_from": "2026-07-01",
                "date_to": "2026-07-15",
                "metric_codes": ["unknown_metric"],
            },
        )
        generated_invalid_metric = generate_report_job(invalid_metric_job.id)
        self.assertEqual(generated_invalid_metric.status, ReportJobStatus.FAILED)
        self.assertIn("Invalid metric_codes value", generated_invalid_metric.error_message)

        over_limit_job = self._job(
            template=template,
            parameters={
                "report_type": "telemetry_export",
                "date_from": "2026-01-01",
                "date_to": "2026-03-01",
                "metric_codes": ["roomTemp"],
            },
        )
        generated_over_limit = generate_report_job(over_limit_job.id)
        self.assertEqual(generated_over_limit.status, ReportJobStatus.FAILED)
        self.assertIn("Date range cannot exceed 31 days", generated_over_limit.error_message)

    def test_telemetry_export_empty_dataset_still_produces_headers(self):
        template = self._template(
            code="TELEMETRY_EXPORT_EMPTY",
            report_type="telemetry_export",
            config={
                "report_type": "telemetry_export",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": ["date_from", "date_to", "metric_codes"],
                "optional_filters": ["device_id", "device_model_id", "device_type_id", "data_center_id", "room_id", "rack_id"],
                "default_columns": [
                    "timestamp",
                    "organization",
                    "data_center",
                    "room",
                    "rack",
                    "device",
                    "device_model",
                    "device_type",
                    "metric_code",
                    "metric_name",
                    "value",
                    "unit",
                    "quality",
                ],
                "max_date_range_days": 31,
            },
        )
        MetricDefinition.objects.create(
            code="empty_metric",
            name="Empty Metric",
            category=MetricCategory.ENVIRONMENT,
            data_type=MetricDataType.FLOAT,
            unit="",
        )
        job = self._job(
            template=template,
            parameters={
                "report_type": "telemetry_export",
                "date_from": "2026-07-01",
                "date_to": "2026-07-15",
                "metric_codes": ["empty_metric"],
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("timestamp,organization,data_center,room,rack,device,device_model,device_type,metric_code,metric_name,value,unit,quality", content)
        self.assertEqual(len(content.strip().splitlines()), 1)

    def test_invalid_date_range_fails_cleanly(self):
        template = self._template(code="INVALID_DATE_TEMPLATE", report_type="alert_summary")
        job = self._job(
            template=template,
            parameters={
                "report_type": "alert_summary",
                "date_from": "2026-07-10",
                "date_to": "2026-07-01",
            },
        )
        generated = generate_report_job(job.id)
        self.assertEqual(generated.status, ReportJobStatus.FAILED)
        self.assertIn("Invalid date range", generated.error_message)

    def test_report_templates_accept_extended_config(self):
        template = self._template(
            code="EXTENDED_TEMPLATE",
            report_type="alert_summary",
            config={
                "report_type": "alert_summary",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": [],
                "optional_filters": ["date_from", "date_to"],
                "default_columns": ["section", "label", "value"],
                "max_date_range_days": 90,
            },
        )
        self.assertEqual(template.report_type, "alert_summary")
        self.assertEqual(template.config["allowed_output_formats"], ["csv"])

    def test_device_inventory_supports_extended_filters(self):
        template = self._template(
            code="DEVICE_INVENTORY_EXTENDED",
            report_type="device_inventory",
            config={
                "report_type": "device_inventory",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": [],
                "optional_filters": ["device_id", "device_model_id", "device_type_id", "status", "is_active"],
                "default_columns": [
                    "organization",
                    "data_center",
                    "room",
                    "rack",
                    "device",
                    "code",
                    "hostname",
                    "ip_address",
                    "device_type",
                    "device_model",
                    "vendor",
                    "status",
                    "is_active",
                    "last_seen",
                ],
            },
        )
        extra_device = Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            room=None,
            rack=None,
            device_type=self.device_type,
            device_model=self.device_model,
            name="UPS-EXT",
            code="UPS-EXT",
            hostname="ups-ext.local",
            ip_address="10.10.10.99",
            status="ONLINE",
            is_active=True,
        )
        job = self._job(
            template=template,
            parameters={
                "report_type": "device_inventory",
                "device_id": str(extra_device.id),
                "device_model_id": str(self.device_model.id),
                "status": "ONLINE",
                "is_active": True,
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("device_id,organization,data_center,room,rack,device,code,hostname,ip_address,device_type,device_model,vendor,status,is_active,last_seen", content)
        self.assertIn("UPS-EXT", content)
        self.assertNotIn("UPS-01", content)

    def test_notification_delivery_supports_extended_filters(self):
        template = self._template(
            code="NOTIFICATION_DELIVERY_EXTENDED",
            report_type="notification_delivery",
            config={
                "report_type": "notification_delivery",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": [],
                "optional_filters": ["date_from", "date_to", "recipient_id", "channel", "status", "alert_id", "device_id"],
                "default_columns": ["section", "label", "value"],
                "max_date_range_days": 90,
            },
        )
        now = timezone.now()
        Notification.objects.create(
            organization=self.org,
            recipient=self.user,
            channel=NotificationChannel.WEB,
            subject="Ignored notification",
            message="Ignored",
            status=NotificationStatus.PENDING,
        )
        notification = Notification.objects.latest("created_at")
        Notification.objects.filter(pk=notification.pk).update(created_at=now - timedelta(hours=1))

        job = self._job(
            template=template,
            parameters={
                "report_type": "notification_delivery",
                "date_from": (now - timedelta(hours=2)).isoformat(),
                "date_to": (now + timedelta(hours=2)).isoformat(),
                "recipient_id": str(self.user.id),
                "channel": ["WEB"],
                "status": "PENDING",
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("summary,total,1", content)
        self.assertIn("summary,pending,1", content)
        self.assertIn("channel,WEB,1", content)

    def test_alert_export_generates_rows_and_respects_scope(self):
        template = self._template(
            code="ALERT_EXPORT_TEMPLATE",
            report_type="alert_export",
            config={
                "report_type": "alert_export",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": ["date_from", "date_to"],
                "optional_filters": ["device_id", "device_model_id", "device_type_id", "data_center_id", "room_id", "rack_id", "metric_codes", "severity", "status", "source"],
                "default_columns": [
                    "triggered_at",
                    "resolved_at",
                    "organization",
                    "data_center",
                    "room",
                    "rack",
                    "device",
                    "device_model",
                    "metric",
                    "severity",
                    "status",
                    "message",
                    "occurrence_count",
                    "acknowledged_by",
                    "resolved_by",
                ],
                "max_date_range_days": 90,
            },
        )
        metric = MetricDefinition.objects.create(
            code="alert_export_metric",
            name="Alert Export Metric",
            category=MetricCategory.ALARM,
            data_type=MetricDataType.FLOAT,
            unit="",
        )
        now = timezone.now()
        AlertEvent.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=metric,
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.OPEN,
            message="Recent alert export row",
            triggered_at=now - timedelta(hours=1),
            occurrence_count=2,
            acknowledged_by=self.user,
            resolved_by=self.other_user,
        )
        AlertEvent.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=metric,
            severity=AlertSeverity.WARNING,
            status=AlertStatus.RESOLVED,
            message="Old alert export row",
            triggered_at=now - timedelta(days=10),
        )
        AlertEvent.objects.create(
            organization=self.other_org,
            data_center=self.other_dc,
            device=self.other_device,
            metric=metric,
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.OPEN,
            message="Other org alert",
            triggered_at=now - timedelta(hours=1),
        )
        job = self._job(
            template=template,
            parameters={
                "report_type": "alert_export",
                "date_from": (now - timedelta(hours=2)).isoformat(),
                "date_to": (now + timedelta(hours=2)).isoformat(),
                "device_id": str(self.device.id),
                "severity": ["CRITICAL"],
                "status": ["OPEN"],
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("triggered_at,resolved_at,organization,data_center,room,rack,device,device_model,metric,severity,status,message,occurrence_count,acknowledged_by,resolved_by", content)
        self.assertIn("Recent alert export row", content)
        self.assertNotIn("Old alert export row", content)
        self.assertNotIn("Other org alert", content)

    def test_audit_export_generates_rows_and_respects_scope(self):
        template = self._template(
            code="AUDIT_EXPORT_TEMPLATE",
            report_type="audit_export",
            config={
                "report_type": "audit_export",
                "output_format": "csv",
                "allowed_output_formats": ["csv"],
                "required_filters": ["date_from", "date_to"],
                "optional_filters": ["actor_id", "actions", "resource_type", "resource_id", "ip_address"],
                "default_columns": [
                    "created_at",
                    "actor",
                    "action",
                    "resource_type",
                    "resource_id",
                    "organization",
                    "message",
                    "ip_address",
                    "user_agent",
                ],
                "max_date_range_days": 90,
            },
        )
        now = timezone.now()
        AuditLog.objects.create(
            organization=self.org,
            actor=self.user,
            action=AuditAction.ALERT_ACKNOWLEDGED,
            resource_type="AlertEvent",
            resource_id=str(self.device.id),
            message="Recent audit export row",
            ip_address="10.0.0.1",
            user_agent="pytest",
        )
        recent_log = AuditLog.objects.latest("created_at")
        AuditLog.objects.filter(pk=recent_log.pk).update(created_at=now - timedelta(hours=1))
        AuditLog.objects.create(
            organization=self.org,
            actor=self.user,
            action=AuditAction.REPORT_DOWNLOADED,
            resource_type="ReportJob",
            resource_id="old",
            message="Old audit export row",
            ip_address="10.0.0.2",
            user_agent="pytest",
        )
        old_log = AuditLog.objects.latest("created_at")
        AuditLog.objects.filter(pk=old_log.pk).update(created_at=now - timedelta(days=5))

        job = self._job(
            template=template,
            parameters={
                "report_type": "audit_export",
                "date_from": (now - timedelta(hours=2)).isoformat(),
                "date_to": (now + timedelta(hours=2)).isoformat(),
                "actor_id": str(self.user.id),
                "actions": ["ALERT_ACKNOWLEDGED"],
            },
        )
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("created_at,actor,action,resource_type,resource_id,organization,message,ip_address,user_agent", content)
        self.assertIn("Recent audit export row", content)
        self.assertNotIn("Old audit export row", content)

    def test_seed_report_templates_command_creates_updates_without_duplicates(self):
        ReportTemplate.objects.filter(organization=self.org).delete()
        call_command("seed_report_templates", organization_code=self.org.code)
        self.assertEqual(ReportTemplate.objects.filter(organization=self.org).count(), 6)

        changed = ReportTemplate.objects.get(organization=self.org, code="ALERT_SUMMARY")
        changed.name = "Changed Name"
        changed.save(update_fields=["name", "updated_at"])

        call_command("seed_report_templates", organization_code=self.org.code)
        self.assertEqual(ReportTemplate.objects.filter(organization=self.org).count(), 6)
        self.assertEqual(ReportTemplate.objects.get(organization=self.org, code="ALERT_SUMMARY").name, "Alert Summary")

    def test_seed_report_templates_requires_organization_code_when_ambiguous(self):
        with self.assertRaises(CommandError):
            call_command("seed_report_templates")
