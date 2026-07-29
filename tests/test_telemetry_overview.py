import pytest
from rest_framework.test import APIClient
from django.utils import timezone

from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition


@pytest.mark.django_db
def test_latest_telemetry_overview_returns_compact_rows():
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

    LatestTelemetry.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=load_metric,
        value_float=42.5,
        quality="GOOD",
        last_seen_at=timezone.now(),
    )
    LatestTelemetry.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=battery_metric,
        value_float=88.0,
        quality="GOOD",
        last_seen_at=timezone.now(),
    )

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/telemetry/latest/overview/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    rows = payload["results"]
    assert {row["metric_code"] for row in rows} == {"ups_load_percent", "ups_battery_charge"}
    assert all(row["device_id"] == str(device.pk) for row in rows)
    assert all("metric" not in row for row in rows)
