from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceCategory, DeviceType
from apps.organizations.models import Organization
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition, TelemetryPoint


class TelemetryHistoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="telemetry-user", password="test12345", is_active=True)
        self.role = Role.objects.create(code="TELEMETRY_VIEWER", name="Telemetry Viewer", scope=RoleScope.ORGANIZATION, status="ACTIVE")
        permission = Permission.objects.create(code="telemetry.view", module="telemetry", description="View telemetry")
        RolePermission.objects.create(role=self.role, permission=permission)

        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.other_org = Organization.objects.create(name="Org Two", code="ORG-2")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code="DC-2")
        self.device_type = DeviceType.objects.create(name="UPS", code="UPS", category=DeviceCategory.POWER)
        self.metric = MetricDefinition.objects.create(
            code="ups_load_percent",
            name="UPS Load Percent",
            category=MetricCategory.POWER,
            data_type=MetricDataType.FLOAT,
            unit="%",
        )
        self.device = Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            device_type=self.device_type,
            name="UPS-01",
            code="UPS-01",
        )
        self.other_device = Device.objects.create(
            organization=self.other_org,
            data_center=self.other_dc,
            device_type=self.device_type,
            name="UPS-02",
            code="UPS-02",
        )

        UserResourceAccess.objects.create(
            user=self.user,
            role=self.role,
            organization=self.org,
            is_active=True,
        )

        now = timezone.make_aware(datetime(2026, 7, 12, 16, 30, 0), timezone.get_current_timezone())
        self.now = now
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=self.metric,
            time=now - timedelta(hours=4),
            value_float=31.0,
            quality="GOOD",
            source="snmp",
        )
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=self.metric,
            time=now - timedelta(hours=2),
            value_float=35.0,
            quality="GOOD",
            source="snmp",
        )
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=self.metric,
            time=now - timedelta(minutes=30),
            value_float=41.0,
            quality="GOOD",
            source="snmp",
        )
        TelemetryPoint.objects.create(
            organization=self.other_org,
            data_center=self.other_dc,
            device=self.other_device,
            metric=self.metric,
            time=now - timedelta(minutes=30),
            value_float=99.0,
            quality="GOOD",
            source="snmp",
        )

    def test_history_returns_only_points_in_requested_range(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            "/api/v1/telemetry/points/history/",
            {
                "device": str(self.device.id),
                "metric_code": "ups_load_percent",
                "date_from": (self.now - timedelta(hours=5)).isoformat(),
                "date_to": self.now.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 6)
        values = [row["value"] for row in response.json()["results"]]
        self.assertEqual(values, [None, 31.0, None, 35.0, None, 41.0])

    def test_history_is_scoped_by_organization(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            "/api/v1/telemetry/points/history/",
            {
                "device": str(self.other_device.id),
                "metric_code": "ups_load_percent",
                "date_from": (self.now - timedelta(hours=5)).isoformat(),
                "date_to": self.now.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        self.assertEqual(response.json()["results"], [])

    def test_history_rejects_invalid_date_range(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            "/api/v1/telemetry/points/history/",
            {
                "device": str(self.device.id),
                "metric_code": "ups_load_percent",
                "date_from": self.now.isoformat(),
                "date_to": (self.now - timedelta(hours=1)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 400)
