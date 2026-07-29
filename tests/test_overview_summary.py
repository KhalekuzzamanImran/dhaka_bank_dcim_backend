from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition


@pytest.mark.django_db
def test_data_center_overview_summary_returns_combined_payload():
    user = User.objects.create_superuser(username="admin", email="admin@example.com", password="pass")
    org = Organization.objects.create(name="Org", code="ORG")
    dc = DataCenter.objects.create(organization=org, name="DC", code="DC-1")
    device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
    vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
    model = DeviceModel.objects.create(vendor=vendor, device_type=device_type, name="UPS Model", model_number="UPS-1")
    device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=device_type,
        device_model=model,
        name="UPS-01",
        code="UPS-01",
        ip_address="10.10.10.10",
    )

    load_metric = MetricDefinition.objects.create(
        code="ups_load_percent",
        name="UPS Load Percent",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.FLOAT,
        is_active=True,
    )
    battery_metric = MetricDefinition.objects.create(
        code="ups_battery_charge",
        name="UPS Battery Charge",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.FLOAT,
        is_active=True,
    )

    now = timezone.now()
    LatestTelemetry.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=load_metric,
        value_float=42.5,
        quality="GOOD",
        last_seen_at=now,
    )
    LatestTelemetry.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=battery_metric,
        value_float=88.0,
        quality="GOOD",
        last_seen_at=now,
    )

    AlertEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=load_metric,
        severity=AlertSeverity.WARNING,
        status=AlertStatus.OPEN,
        message="UPS load warning",
        triggered_at=now,
        last_seen_at=now,
    )
    AlertEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=battery_metric,
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.RESOLVED,
        message="UPS battery recovered",
        triggered_at=now,
        resolved_at=now,
    )

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/overview-summary/")

    assert response.status_code == 200
    payload = response.json()

    assert len(payload["devices"]) == 1
    assert payload["devices"][0]["device_type"] == str(device_type.pk)
    assert payload["devices"][0]["device_type_name"] == "UPS"

    assert len(payload["device_types"]) == 1
    assert payload["device_types"][0]["device_count"] == 1
    assert payload["device_types"][0]["code"] == "UPS"

    assert len(payload["latest_telemetry"]) == 2
    assert {row["metric_code"] for row in payload["latest_telemetry"]} == {"ups_load_percent", "ups_battery_charge"}

    assert len(payload["active_alerts"]) == 1
    assert payload["active_alerts"][0]["status"] == AlertStatus.OPEN
    assert payload["alert_summary"]["open_total"] == 1
    assert payload["alert_summary"]["critical_open"] == 0
    assert payload["alert_summary"]["warning_open"] == 1
    assert payload["alert_summary"]["resolved_today"] == 1
