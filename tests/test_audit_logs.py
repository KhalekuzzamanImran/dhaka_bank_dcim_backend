from __future__ import annotations

import os
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.alerts.models import AlertEvent, AlertRule, AlertSeverity, AlertStatus
from apps.alerts.services import evaluate_latest
from apps.audit.admin import AuditLogAdmin
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from apps.common.audit import write_audit
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.reports.models import ReportJob, ReportJobStatus, ReportTemplate
from apps.reports.services.generator import generate_report_job
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition


class AuditLogTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="admin", email="admin@example.com", password="test12345")
        self.user_a = User.objects.create_user(username="user-a", email="user-a@example.com", password="test12345", is_active=True)
        self.user_b = User.objects.create_user(username="user-b", email="user-b@example.com", password="test12345", is_active=True)

        self.role_a = self._role("AUDIT_ROLE_A", "Audit Role A")
        self.role_b = self._role("AUDIT_ROLE_B", "Audit Role B")
        self._grant_permissions(
            self.role_a,
            [
                "audit.view",
                "device.view",
                "device.create",
                "device.update",
                "device.delete",
                "alert.view",
                "alert.acknowledge",
                "alert.resolve",
                "report.view",
                "report.generate",
                "report.download",
            ],
        )
        self._grant_permissions(self.role_b, ["audit.view"])

        self.org_a = Organization.objects.create(name="Org A", code="ORG-A")
        self.org_b = Organization.objects.create(name="Org B", code="ORG-B")
        self.dc_a = DataCenter.objects.create(organization=self.org_a, name="DC A", code="DC-A")
        self.dc_b = DataCenter.objects.create(organization=self.org_b, name="DC B", code="DC-B")

        self.device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
        self.vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name="Model",
            model_number="M1",
        )

        self.device_a = Device.objects.create(
            organization=self.org_a,
            data_center=self.dc_a,
            device_type=self.device_type,
            device_model=self.device_model,
            name="Device A",
            code="DEV-A",
        )
        self.device_b = Device.objects.create(
            organization=self.org_b,
            data_center=self.dc_b,
            device_type=self.device_type,
            device_model=self.device_model,
            name="Device B",
            code="DEV-B",
        )

        self.metric = MetricDefinition.objects.create(
            code="AUDIT_METRIC",
            name="Audit Metric",
            category=MetricCategory.STATUS,
            data_type=MetricDataType.INTEGER,
            is_active=True,
        )

        self._assign_access(self.user_a, self.role_a, organization=self.org_a)
        self._assign_access(self.user_b, self.role_b, organization=self.org_b)

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

    def _assign_access(self, user, role, *, organization):
        UserResourceAccess.objects.create(
            user=user,
            role=role,
            organization=organization,
            assigned_by=self.admin,
            is_active=True,
        )

    def _create_alert(self, device, status=AlertStatus.OPEN):
        rule = AlertRule.objects.create(
            organization=device.organization,
            device=device,
            metric=self.metric,
            name="Audit Alert Rule",
            operator="EQ",
            threshold_integer=1,
            severity=AlertSeverity.CRITICAL,
            duration_seconds=0,
            is_active=True,
        )
        latest = LatestTelemetry.objects.create(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            metric=self.metric,
            value_integer=1,
            quality="GOOD",
            last_seen_at=timezone.now(),
            source="test",
        )
        evaluate_latest(latest)
        alert = AlertEvent.objects.get(device=device, metric=self.metric, alert_rule=rule)
        if status != AlertStatus.OPEN:
            alert.status = status
            alert.save(update_fields=["status", "updated_at"])
        return alert

    def _create_report(self, organization, data_center, requested_by):
        template = ReportTemplate.objects.create(
            organization=organization,
            name="Device Inventory",
            code=f"REPORT-{organization.code}",
            description="Test template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            is_active=True,
        )
        return ReportJob.objects.create(
            organization=organization,
            data_center=data_center,
            template=template,
            requested_by=requested_by,
            parameters={"report_type": "device_inventory"},
            status=ReportJobStatus.PENDING,
        )

    def tearDown(self):
        for job in ReportJob.objects.all():
            if job.file:
                try:
                    path = job.file.path
                    job.file.delete(save=False)
                    if path and os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass

    def test_serializer_returns_display_fields_and_redacts_values(self):
        log = AuditLog.objects.create(
            organization=self.org_a,
            actor=self.user_a,
            action="UPDATE",
            resource_type="Device",
            resource_id="123",
            old_value={"password": "secret", "nested": {"api_key": "abc123"}},
            new_value={"token": "new-token", "items": [{"community": "public"}]},
            ip_address="127.0.0.1",
            user_agent="pytest",
            message="updated",
        )

        payload = AuditLogSerializer(log).data

        self.assertEqual(payload["organization_name"], self.org_a.name)
        self.assertEqual(payload["actor_name"], self.user_a.username)
        self.assertEqual(payload["actor_email"], self.user_a.email)
        self.assertEqual(payload["old_value"]["password"], "***REDACTED***")
        self.assertEqual(payload["old_value"]["nested"]["api_key"], "***REDACTED***")
        self.assertEqual(payload["new_value"]["token"], "***REDACTED***")
        self.assertEqual(payload["new_value"]["items"][0]["community"], "***REDACTED***")

    def test_write_audit_redacts_nested_sensitive_values(self):
        write_audit(
            "UPDATE",
            "Device",
            "device-1",
            old_value={
                "password": "secret",
                "nested": {"api_key": "nested-key", "child": [{"priv_key": "priv"}]},
            },
            new_value={"token": "abc", "credential": {"community": "public"}},
            organization=self.org_a,
            actor=self.user_a,
        )

        log = AuditLog.objects.get(resource_id="device-1")
        self.assertEqual(log.old_value["password"], "***REDACTED***")
        self.assertEqual(log.old_value["nested"]["api_key"], "***REDACTED***")
        self.assertEqual(log.old_value["nested"]["child"][0]["priv_key"], "***REDACTED***")
        self.assertEqual(log.new_value["token"], "***REDACTED***")
        self.assertEqual(log.new_value["credential"]["community"], "***REDACTED***")

    def test_audit_api_is_read_only(self):
        self.client.force_authenticate(user=self.user_a)
        log = write_audit("CREATE", "Device", "read-only", organization=self.org_a, actor=self.user_a)
        response = self.client.post("/api/v1/audit/audit-logs/", {}, format="json")
        self.assertIn(response.status_code, {403, 405})

        detail_response = self.client.put(f"/api/v1/audit/audit-logs/{log.id}/", {}, format="json")
        patch_response = self.client.patch(f"/api/v1/audit/audit-logs/{log.id}/", {}, format="json")
        delete_response = self.client.delete(f"/api/v1/audit/audit-logs/{log.id}/")
        self.assertIn(detail_response.status_code, {403, 405})
        self.assertIn(patch_response.status_code, {403, 405})
        self.assertIn(delete_response.status_code, {403, 405})

    def test_audit_api_scopes_by_organization(self):
        write_audit("CREATE", "Device", "a1", organization=self.org_a, actor=self.user_a)
        write_audit("CREATE", "Device", "b1", organization=self.org_b, actor=self.user_b)

        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/api/v1/audit/audit-logs/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        results = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        self.assertTrue(any(row["organization"] == str(self.org_a.id) for row in results))
        self.assertFalse(any(row["organization"] == str(self.org_b.id) for row in results))

    def test_user_from_org_a_cannot_see_org_b_audit_logs(self):
        write_audit("UPDATE", "Device", "org-a", organization=self.org_a, actor=self.user_a)
        write_audit("UPDATE", "Device", "org-b", organization=self.org_b, actor=self.user_b)

        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/api/v1/audit/audit-logs/")
        payload = response.json()
        results = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        self.assertFalse(any(row["resource_id"] == "org-b" for row in results))

    def test_alert_acknowledge_and_resolve_create_audit_entries(self):
        alert = self._create_alert(self.device_a)
        self.client.force_authenticate(user=self.user_a)

        ack_response = self.client.post(
            f"/api/v1/alerts/alert-events/{alert.id}/acknowledge/",
            {"comment": "checking"},
            format="json",
        )
        self.assertEqual(ack_response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action="ALERT_ACKNOWLEDGED",
                resource_type="AlertEvent",
                resource_id=str(alert.id),
                organization=self.org_a,
            ).exists()
        )

        resolve_response = self.client.post(
            f"/api/v1/alerts/alert-events/{alert.id}/resolve/",
            {"comment": "done"},
            format="json",
        )
        self.assertEqual(resolve_response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action="ALERT_RESOLVED",
                resource_type="AlertEvent",
                resource_id=str(alert.id),
                organization=self.org_a,
            ).exists()
        )

    def test_report_generation_and_download_create_audit_entries(self):
        job = self._create_report(self.org_a, self.dc_a, self.user_a)
        self.client.force_authenticate(user=self.user_a)

        with patch("apps.reports.tasks.generate_report_job_task.delay", side_effect=lambda job_id: generate_report_job(job_id)):
            generate_response = self.client.post(f"/api/v1/reports/report-jobs/{job.id}/generate/", {}, format="json")
        self.assertEqual(generate_response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action="REPORT_GENERATION_REQUESTED",
                resource_type="ReportJob",
                resource_id=str(job.id),
                organization=self.org_a,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="REPORT_GENERATED",
                resource_type="ReportJob",
                resource_id=str(job.id),
                organization=self.org_a,
            ).exists()
        )

        download_response = self.client.get(f"/api/v1/reports/report-jobs/{job.id}/download/")
        self.assertEqual(download_response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action="REPORT_DOWNLOADED",
                resource_type="ReportJob",
                resource_id=str(job.id),
                organization=self.org_a,
            ).exists()
        )

    def test_device_create_update_delete_write_audit_logs(self):
        self.client.force_authenticate(user=self.user_a)
        payload = {
            "organization": str(self.org_a.id),
            "data_center": str(self.dc_a.id),
            "device_type": str(self.device_type.id),
            "device_model": str(self.device_model.id),
            "name": "Device API",
            "code": "DEV-API",
            "status": "OFFLINE",
            "is_active": True,
        }
        create_response = self.client.post("/api/v1/devices/devices/", payload, format="json")
        self.assertEqual(create_response.status_code, 201)
        device_id = create_response.json()["id"]
        self.assertTrue(
            AuditLog.objects.filter(
                action="CREATE",
                resource_type="Device",
                resource_id=device_id,
                organization=self.org_a,
            ).exists()
        )

        update_response = self.client.patch(f"/api/v1/devices/devices/{device_id}/", {"name": "Device API Updated"}, format="json")
        self.assertEqual(update_response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action="UPDATE",
                resource_type="Device",
                resource_id=device_id,
                organization=self.org_a,
            ).exists()
        )

        delete_response = self.client.delete(f"/api/v1/devices/devices/{device_id}/")
        self.assertIn(delete_response.status_code, {200, 204})
        self.assertTrue(
            AuditLog.objects.filter(
                action="DELETE",
                resource_type="Device",
                resource_id=device_id,
                organization=self.org_a,
            ).exists()
        )

    def test_admin_is_read_only(self):
        admin = AuditLogAdmin(AuditLog, admin_site=AdminSite())
        request = type("Request", (), {"method": "GET"})()
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_delete_permission(request))
        self.assertTrue(admin.has_change_permission(request))
