from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.organizations.models import Organization
from apps.reports.enums import ReportDeliveryStatus, ReportScheduleStatus, ReportTriggerSource
from apps.reports.models import (
    ReportArtifact,
    ReportDefinition,
    ReportDelivery,
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleDelivery,
    ReportScheduleRecipient,
    ReportScheduleRun,
    ReportScheduleDeliveryStatus,
    ReportScheduleRunStatus,
    ReportTemplate,
)
from apps.reports.services.definitions import seed_report_definitions
from apps.reports.services.factory import create_report_job
from apps.reports.services.generator import generate_report_job


def _perm(code):
    return Permission.objects.get_or_create(
        code=code,
        defaults={"module": code.split(".")[0], "description": code},
    )[0]


def _role(code, name, scope, perm_codes):
    role, _ = Role.objects.update_or_create(
        code=code,
        defaults={"name": name, "scope": scope, "status": "ACTIVE"},
    )
    for perm_code in perm_codes:
        RolePermission.objects.get_or_create(role=role, permission=_perm(perm_code))
    return role


@override_settings(REPORT_ARTIFACT_RETENTION_DAYS=14)
class ReportPhase6BDashboardTestCase(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="reports-dashboard-")
        self._override = override_settings(MEDIA_ROOT=self.media_root)
        self._override.enable()

        seed_report_definitions()
        self.client = APIClient()
        self.tz = ZoneInfo("Asia/Dhaka")
        self.user = User.objects.create_user(username=f"dashboard-user-{uuid.uuid4().hex[:8]}", password="test12345", is_active=True)
        self.limited_user = User.objects.create_user(username=f"limited-user-{uuid.uuid4().hex[:8]}", password="test12345", is_active=True)
        self.dashboard_role = _role("REPORT_DASHBOARD", "Report Dashboard", RoleScope.ORGANIZATION, ["report.view"])

        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.other_org = Organization.objects.create(name="Org Two", code="ORG-2")
        self.blocked_org = Organization.objects.create(name="Org Three", code="ORG-3")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code="DC-2")

        UserResourceAccess.objects.create(
            user=self.user,
            role=self.dashboard_role,
            organization=self.org,
            assigned_by=self.user,
            is_active=True,
        )
        UserResourceAccess.objects.create(
            user=self.user,
            role=self.dashboard_role,
            organization=self.other_org,
            assigned_by=self.user,
            is_active=True,
        )
        UserResourceAccess.objects.create(
            user=self.limited_user,
            role=self.dashboard_role,
            organization=self.org,
            assigned_by=self.user,
            is_active=True,
        )

        self.definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")
        self.template_csv = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory CSV",
            code="TPL-CSV",
            description="CSV template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={},
            primary_format="CSV",
            attachment_formats=[],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )
        self.template_pdf = ReportTemplate.objects.create(
            organization=self.org,
            definition=self.definition,
            name="Inventory PDF",
            code="TPL-PDF",
            description="PDF template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={},
            primary_format="PDF",
            attachment_formats=["CSV"],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )

        self.today = timezone.localtime(timezone.now(), self.tz).date()
        self.day_1 = self.today - timedelta(days=2)
        self.day_2 = self.today - timedelta(days=1)
        self.day_3 = self.today

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _aware(self, day, hour, minute=0):
        return timezone.make_aware(datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute), self.tz)

    def _stamp_job(self, job: ReportJob, *, created_at, started_at=None, completed_at=None, failed_at=None, cancelled_at=None, queued_at=None):
        fields = {"created_at": created_at, "updated_at": completed_at or failed_at or cancelled_at or started_at or created_at}
        if started_at is not None:
            fields["started_at"] = started_at
        if completed_at is not None:
            fields["completed_at"] = completed_at
        if failed_at is not None:
            fields["failed_at"] = failed_at
        if cancelled_at is not None:
            fields["cancelled_at"] = cancelled_at
        if queued_at is not None:
            fields["queued_at"] = queued_at
        ReportJob.objects.filter(pk=job.pk).update(**fields)
        return ReportJob.objects.get(pk=job.pk)

    def _stamp_artifacts(self, job: ReportJob, created_at):
        ReportArtifact.objects.filter(job=job).update(created_at=created_at, updated_at=created_at)

    def _create_completed_job(self, *, template, day, hour, queue_job=False):
        result = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={"report_type": "device_inventory"},
            queue_job=queue_job,
        )
        job = result.job
        with override_settings(MEDIA_ROOT=self.media_root):
            from unittest.mock import patch

            with patch("apps.reports.services.execution._queue_report_deliveries", lambda job_id: None):
                generated = generate_report_job(job.id)
        created_at = self._aware(day, hour)
        started_at = created_at - timedelta(minutes=5)
        completed_at = created_at + timedelta(minutes=5)
        generated = self._stamp_job(generated, created_at=created_at, started_at=started_at, completed_at=completed_at)
        self._stamp_artifacts(generated, created_at)
        return generated

    def _create_failed_job(self, *, template, day, hour, error_code="GEN_FAIL", error_message="Generation failed"):
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
        job = result.job
        created_at = self._aware(day, hour)
        started_at = created_at - timedelta(minutes=5)
        completed_at = created_at + timedelta(minutes=1)
        job = self._stamp_job(
            job,
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            failed_at=completed_at,
        )
        ReportJob.objects.filter(pk=job.pk).update(status=ReportJobStatus.FAILED, error_code=error_code, error_message=error_message)
        return ReportJob.objects.get(pk=job.pk)

    def _create_running_job(self, *, template, day, hour):
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
        job = self._stamp_job(
            result.job,
            created_at=self._aware(day, hour),
            started_at=self._aware(day, hour),
        )
        ReportJob.objects.filter(pk=job.pk).update(status=ReportJobStatus.RUNNING, progress_percent=40, progress_message="Running")
        return ReportJob.objects.get(pk=job.pk)

    def _create_queued_job(self, *, template, day, hour):
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
        job = self._stamp_job(result.job, created_at=self._aware(day, hour), queued_at=self._aware(day, hour))
        ReportJob.objects.filter(pk=job.pk).update(status=ReportJobStatus.QUEUED, progress_percent=5, progress_message="Queued")
        return ReportJob.objects.get(pk=job.pk)

    def _create_cancelled_job(self, *, template, day, hour):
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
        created_at = self._aware(day, hour)
        job = self._stamp_job(
            result.job,
            created_at=created_at,
            started_at=created_at,
            completed_at=created_at + timedelta(minutes=1),
            cancelled_at=created_at + timedelta(minutes=1),
        )
        ReportJob.objects.filter(pk=job.pk).update(status=ReportJobStatus.CANCELLED, error_message="Cancelled")
        return ReportJob.objects.get(pk=job.pk)

    def _create_delivery(self, *, job, channel, recipient, status, day, hour, error_message="", error_code="", provider_response=None):
        timestamp = self._aware(day, hour)
        payload = {
            "job": job,
            "channel": channel,
            "recipient": recipient,
            "status": status,
            "queued_at": timestamp if status in {ReportDeliveryStatus.QUEUED, ReportDeliveryStatus.DELIVERING, ReportDeliveryStatus.SENT, ReportDeliveryStatus.FAILED} else None,
            "started_at": timestamp if status in {ReportDeliveryStatus.DELIVERING, ReportDeliveryStatus.SENT, ReportDeliveryStatus.FAILED} else None,
            "sent_at": timestamp if status == ReportDeliveryStatus.SENT else None,
            "failed_at": timestamp if status == ReportDeliveryStatus.FAILED else None,
            "error_message": error_message,
            "error_code": error_code,
            "provider_response": provider_response or {},
        }
        return ReportDelivery.objects.create(**payload)

    def _dashboard(self, user=None, **params):
        client = APIClient()
        client.force_authenticate(user=user or self.user)
        return client.get("/api/v1/reports/dashboard/", params)

    def _build_dashboard_data(self):
        completed_csv = self._create_completed_job(template=self.template_csv, day=self.day_1, hour=9)
        completed_pdf = self._create_completed_job(template=self.template_pdf, day=self.day_3, hour=15)
        failed_job = self._create_failed_job(template=self.template_csv, day=self.day_1, hour=8)
        running_job = self._create_running_job(template=self.template_csv, day=self.day_3, hour=16)
        queued_job = self._create_queued_job(template=self.template_csv, day=self.day_3, hour=14)
        cancelled_job = self._create_cancelled_job(template=self.template_csv, day=self.day_3, hour=13)
        legacy_job = ReportJob.objects.create(
            organization=self.org,
            data_center=self.dc,
            requested_by=self.user,
            status=ReportJobStatus.FAILED,
            parameters={"report_type": "legacy_custom"},
            parameters_snapshot={"report_type": "legacy_custom"},
            template_snapshot={},
            output_config_snapshot={},
            scope_snapshot={},
            recipient_snapshot={},
            source_event_snapshot={},
            trigger_source=ReportTriggerSource.MANUAL,
            started_at=self._aware(self.day_1, 7),
            completed_at=self._aware(self.day_1, 7) + timedelta(minutes=1),
            failed_at=self._aware(self.day_1, 7) + timedelta(minutes=1),
            error_message="Legacy failure",
            error_code="LEGACY_FAIL",
        )
        self._stamp_job(legacy_job, created_at=self._aware(self.day_1, 7), started_at=self._aware(self.day_1, 7), completed_at=self._aware(self.day_1, 7) + timedelta(minutes=1), failed_at=self._aware(self.day_1, 7) + timedelta(minutes=1))

        # Delivery state summary uses the reporting delivery table, not compatibility rows.
        self._create_delivery(
            job=completed_pdf,
            channel="EMAIL",
            recipient="ops@example.com",
            status=ReportDeliveryStatus.SENT,
            day=self.day_3,
            hour=15,
            provider_response={"provider": "smtp", "token": "secret"},
        )
        self._create_delivery(
            job=completed_pdf,
            channel="SMS",
            recipient="01329665857",
            status=ReportDeliveryStatus.FAILED,
            day=self.day_3,
            hour=15,
            error_message="Gateway timeout",
            error_code="SMS_TIMEOUT",
            provider_response={"provider": "sms", "token": "secret"},
        )
        self._create_delivery(
            job=completed_csv,
            channel="EMAIL",
            recipient="ops2@example.com",
            status=ReportDeliveryStatus.PENDING,
            day=self.day_1,
            hour=9,
        )
        self._create_delivery(
            job=running_job,
            channel="EMAIL",
            recipient="running@example.com",
            status=ReportDeliveryStatus.QUEUED,
            day=self.day_3,
            hour=14,
        )
        self._create_delivery(
            job=queued_job,
            channel="SMS",
            recipient="01329660000",
            status=ReportDeliveryStatus.DELIVERING,
            day=self.day_3,
            hour=13,
        )

        active_schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=self.template_pdf,
            name="Active Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=datetime.strptime("06:00", "%H:%M").time(),
            output_format="CSV",
            parameters={"report_type": "device_inventory"},
            parameter_overrides={"asset_scope": "override"},
            recipients=["ops@example.com", "ops@example.com "],
            send_sms=True,
            sms_recipients=["01329665857", " 01329665857 "],
            attachment_formats=["CSV"],
            primary_format="PDF",
            status=ReportScheduleStatus.ACTIVE,
            is_active=True,
            next_run_at=self._aware(self.day_3, 18),
            last_run_at=self._aware(self.day_2, 6),
            last_success_at=self._aware(self.day_2, 6),
            created_by=self.user,
        )
        active_schedule.last_job = failed_job
        active_schedule.save(update_fields=["last_job", "updated_at"])
        paused_schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=self.template_csv,
            name="Paused Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=datetime.strptime("07:00", "%H:%M").time(),
            output_format="CSV",
            parameters={"report_type": "device_inventory"},
            recipients=["paused@example.com"],
            send_sms=False,
            sms_recipients=[],
            attachment_formats=[],
            primary_format="CSV",
            status=ReportScheduleStatus.PAUSED,
            is_active=False,
            next_run_at=self._aware(self.day_3, 19),
            created_by=self.user,
        )
        disabled_schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=self.template_csv,
            name="Disabled Schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=datetime.strptime("08:00", "%H:%M").time(),
            output_format="CSV",
            parameters={"report_type": "device_inventory"},
            recipients=["disabled@example.com"],
            send_sms=False,
            sms_recipients=[],
            attachment_formats=[],
            primary_format="CSV",
            status=ReportScheduleStatus.DISABLED,
            is_active=False,
            next_run_at=self._aware(self.day_3, 20),
            created_by=self.user,
        )
        failed_run = ReportScheduleRun.objects.create(
            schedule=active_schedule,
            organization=self.org,
            requested_by=self.user,
            window_start=self._aware(self.day_2, 5),
            window_end=self._aware(self.day_2, 6),
            scheduled_for=self._aware(self.day_2, 6),
            status=ReportScheduleRunStatus.FAILED,
            queued_at=self._aware(self.day_2, 5),
            started_at=self._aware(self.day_2, 5),
            completed_at=self._aware(self.day_2, 6),
            error_message="Schedule failure",
            trigger_source=ReportTriggerSource.SCHEDULED,
            snapshot={"schedule_id": str(active_schedule.id)},
        )
        ReportScheduleDelivery.objects.create(
            run=failed_run,
            channel="EMAIL",
            status=ReportScheduleDeliveryStatus.SENT,
            recipient_address="ops@example.com",
            queued_at=self._aware(self.day_2, 5),
            delivering_at=self._aware(self.day_2, 5),
            sent_at=self._aware(self.day_2, 6),
            provider_message_id="compat-1",
        )
        ReportScheduleDelivery.objects.create(
            run=failed_run,
            channel="SMS",
            status=ReportScheduleDeliveryStatus.FAILED,
            recipient_address="01329665857",
            queued_at=self._aware(self.day_2, 5),
            delivering_at=self._aware(self.day_2, 5),
            failed_at=self._aware(self.day_2, 6),
            error_message="Compat failure",
        )

        # Legacy schedule delivery rows must not affect dashboard totals.
        compat_only_run = ReportScheduleRun.objects.create(
            schedule=paused_schedule,
            organization=self.org,
            requested_by=self.user,
            window_start=self._aware(self.day_2, 7),
            window_end=self._aware(self.day_2, 8),
            scheduled_for=self._aware(self.day_2, 8),
            status=ReportScheduleRunStatus.COMPLETED,
            queued_at=self._aware(self.day_2, 7),
            started_at=self._aware(self.day_2, 7),
            completed_at=self._aware(self.day_2, 8),
            trigger_source=ReportTriggerSource.SCHEDULED,
            snapshot={"schedule_id": str(paused_schedule.id)},
        )
        ReportScheduleDelivery.objects.create(
            run=compat_only_run,
            channel="EMAIL",
            status=ReportScheduleDeliveryStatus.SENT,
            recipient_address="compat@example.com",
            sent_at=self._aware(self.day_2, 8),
            queued_at=self._aware(self.day_2, 7),
            delivering_at=self._aware(self.day_2, 7),
            provider_message_id="compat-2",
        )

        return {
            "completed_csv": completed_csv,
            "completed_pdf": completed_pdf,
            "failed_job": failed_job,
            "running_job": running_job,
            "queued_job": queued_job,
            "cancelled_job": cancelled_job,
            "legacy_job": legacy_job,
            "active_schedule": active_schedule,
            "paused_schedule": paused_schedule,
            "disabled_schedule": disabled_schedule,
        }

    def test_dashboard_returns_scoped_aggregates_and_trends(self):
        data = self._build_dashboard_data()
        response = self._dashboard(
            start_at=f"{self.day_1.isoformat()}T00:00:00+06:00",
            end_at=f"{self.day_3.isoformat()}T23:59:59+06:00",
            timezone="Asia/Dhaka",
            organization=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["range"]["timezone"], "Asia/Dhaka")
        self.assertEqual(payload["summary"]["total_jobs"], 7)
        self.assertEqual(payload["summary"]["completed_jobs"], 2)
        self.assertEqual(payload["summary"]["running_jobs"], 1)
        self.assertEqual(payload["summary"]["queued_jobs"], 1)
        self.assertEqual(payload["summary"]["failed_jobs"], 2)
        self.assertEqual(payload["summary"]["cancelled_jobs"], 1)
        self.assertEqual(payload["summary"]["total_artifacts"], 3)
        self.assertGreater(payload["summary"]["total_artifact_size_bytes"], 0)
        self.assertEqual(payload["summary"]["total_schedules"], 3)
        self.assertEqual(payload["summary"]["active_schedules"], 1)
        self.assertEqual(payload["summary"]["paused_schedules"], 1)
        self.assertEqual(payload["summary"]["disabled_schedules"], 1)
        self.assertEqual(payload["summary"]["total_deliveries"], 5)
        self.assertEqual(payload["summary"]["sent_deliveries"], 1)
        self.assertEqual(payload["summary"]["failed_deliveries"], 1)
        self.assertEqual(payload["summary"]["delivery_success_rate"], 50.0)
        self.assertEqual(payload["delivery_summary"]["by_status"]["SENT"], 1)
        self.assertEqual(payload["delivery_summary"]["by_status"]["FAILED"], 1)
        self.assertEqual(payload["delivery_summary"]["delivery_success_rate"], 50.0)

        trend = payload["generation_trend"]
        self.assertEqual([row["date"] for row in trend], [self.day_1.isoformat(), self.day_2.isoformat(), self.day_3.isoformat()])
        self.assertEqual(trend[1]["total"], 0)
        self.assertEqual(trend[0]["total"], 3)
        self.assertEqual(trend[2]["total"], 4)

        definitions = {row["code"]: row for row in payload["by_definition"]}
        self.assertIn("DEVICE_INVENTORY", definitions)
        self.assertIn("LEGACY_CUSTOM", definitions)
        self.assertEqual(definitions["DEVICE_INVENTORY"]["completed"], 2)
        self.assertEqual(definitions["LEGACY_CUSTOM"]["failed"], 1)

        formats = {row["format"]: row for row in payload["by_format"]}
        self.assertEqual(formats["CSV"]["count"], 2)
        self.assertEqual(formats["PDF"]["count"], 1)

        self.assertLessEqual(len(payload["recent_jobs"]), 10)
        self.assertEqual(payload["recent_jobs"][0]["status"], ReportJobStatus.RUNNING)
        self.assertIn("PDF", payload["recent_jobs"][1]["artifact_formats"])
        self.assertEqual(payload["recent_jobs"][1]["delivery_summary"]["EMAIL"], ReportDeliveryStatus.SENT)
        self.assertTrue(all("parameters_snapshot" not in row for row in payload["recent_jobs"]))

        upcoming_names = [row["name"] for row in payload["upcoming_schedules"]]
        self.assertEqual(upcoming_names, ["Active Schedule"])
        self.assertEqual(payload["upcoming_schedules"][0]["recipient_count"], 2)
        self.assertIn("EMAIL", payload["upcoming_schedules"][0]["channels"])
        self.assertIn("SMS", payload["upcoming_schedules"][0]["channels"])

        failure_types = [row["type"] for row in payload["recent_failures"]]
        self.assertIn("JOB", failure_types)
        self.assertIn("DELIVERY", failure_types)
        self.assertIn("SCHEDULE_RUN", failure_types)
        delivery_failure = next(row for row in payload["recent_failures"] if row["type"] == "DELIVERY")
        self.assertIn("***", delivery_failure["recipient"])
        self.assertNotIn("01329665857", delivery_failure["recipient"])
        self.assertNotIn("provider_response", delivery_failure)

        self.assertTrue(payload["frequent_templates"])
        self.assertEqual(payload["frequent_templates"][0]["name"], "Inventory CSV")
        self.assertTrue(payload["schedule_health"])
        self.assertIn(payload["schedule_health"][0]["health_status"], {"WARNING", "CRITICAL"})
        self.assertGreaterEqual(payload["operational_health"]["queued_deliveries_over_threshold"], 0)
        self.assertGreaterEqual(payload["operational_health"]["queued_jobs_over_threshold"], 0)
        self.assertIsNotNone(payload["operational_health"]["scheduler_last_activity_at"])

    def test_dashboard_rejects_invalid_scope_timezone_and_range(self):
        client = APIClient()
        client.force_authenticate(user=self.limited_user)

        response = client.get(
            "/api/v1/reports/dashboard/",
            {"organization": str(self.blocked_org.id)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("organization", response.json())

        response = client.get(
            "/api/v1/reports/dashboard/",
            {"organization": str(self.org.id), "data_center": str(self.other_dc.id)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("data_center", response.json())

        response = client.get("/api/v1/reports/dashboard/", {"timezone": "Not/A-Timezone"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("timezone", response.json())

        response = client.get(
            "/api/v1/reports/dashboard/",
            {
                "start_at": "2026-08-03T00:00:00+06:00",
                "end_at": "2026-08-02T00:00:00+06:00",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("end_at", response.json())

        response = client.get(
            "/api/v1/reports/dashboard/",
            {
                "start_at": "2025-01-01T00:00:00+06:00",
                "end_at": "2026-08-03T00:00:00+06:00",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("end_at", response.json())

    def test_dashboard_default_range_and_no_final_delivery_success_rate_none(self):
        ReportDelivery.objects.create(
            job=self._create_queued_job(template=self.template_csv, day=self.day_3, hour=15),
            channel="EMAIL",
            recipient="pending@example.com",
            status=ReportDeliveryStatus.PENDING,
            provider_response={},
        )
        other_template = ReportTemplate.objects.create(
            organization=self.other_org,
            definition=self.definition,
            name="Other Org Template",
            code="TPL-OTHER",
            description="Other org template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={},
            primary_format="CSV",
            attachment_formats=[],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )
        result = create_report_job(
            definition=other_template.definition,
            organization=self.other_org,
            data_center=self.other_dc,
            template=other_template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={"report_type": "device_inventory"},
            queue_job=False,
        )
        other_job = result.job
        ReportDelivery.objects.create(
            job=other_job,
            channel="EMAIL",
            recipient="pending@example.com",
            status=ReportDeliveryStatus.PENDING,
            provider_response={},
        )

        response = self._dashboard(organization=str(self.other_org.id))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        start_at = datetime.fromisoformat(payload["range"]["start_at"])
        end_at = datetime.fromisoformat(payload["range"]["end_at"])
        self.assertGreater(end_at, start_at)
        self.assertLessEqual((end_at - start_at).days, 31)
        self.assertIsNone(payload["summary"]["delivery_success_rate"])
        self.assertEqual(payload["delivery_summary"]["by_status"]["PENDING"], 1)

    def test_dashboard_ignores_report_schedule_delivery_compatibility_rows(self):
        data = self._build_dashboard_data()
        extra_run = ReportScheduleRun.objects.create(
            schedule=data["active_schedule"],
            organization=self.org,
            requested_by=self.user,
            window_start=self._aware(self.day_1, 1),
            window_end=self._aware(self.day_1, 2),
            scheduled_for=self._aware(self.day_1, 2),
            status=ReportScheduleRunStatus.COMPLETED,
            queued_at=self._aware(self.day_1, 1),
            started_at=self._aware(self.day_1, 1),
            completed_at=self._aware(self.day_1, 2),
            trigger_source=ReportTriggerSource.SCHEDULED,
            snapshot={"schedule_id": str(data["active_schedule"].id)},
        )
        ReportScheduleDelivery.objects.create(
            run=extra_run,
            channel="EMAIL",
            status=ReportScheduleDeliveryStatus.SENT,
            recipient_address="compat-only@example.com",
            sent_at=self._aware(self.day_1, 2),
            queued_at=self._aware(self.day_1, 1),
            delivering_at=self._aware(self.day_1, 1),
            provider_message_id="compat-3",
        )

        response = self._dashboard(organization=str(self.org.id))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_deliveries"], 5)
        self.assertEqual(payload["delivery_summary"]["by_status"]["SENT"], 1)
