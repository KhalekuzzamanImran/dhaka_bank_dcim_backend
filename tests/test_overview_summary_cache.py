from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.devices.services.overview_summary import build_data_center_overview_summary
from apps.organizations.models import Organization
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "overview-summary-cache",
        }
    }
)
@pytest.mark.django_db
def test_overview_summary_cache_invalidates_on_telemetry_and_alert_changes():
    cache.clear()

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

    metric = MetricDefinition.objects.create(
        code="ups_load_percent",
        name="UPS Load Percent",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.FLOAT,
        is_active=True,
    )
    latest = LatestTelemetry.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        value_float=42.5,
        quality="GOOD",
        last_seen_at=timezone.now(),
    )
    alert = AlertEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        severity=AlertSeverity.WARNING,
        status=AlertStatus.OPEN,
        message="UPS load warning",
        triggered_at=timezone.now(),
        last_seen_at=timezone.now(),
    )

    first = build_data_center_overview_summary(Device.objects.filter(pk=device.pk), cache_scope="test")
    assert first["latest_telemetry"][0]["value_float"] == 42.5
    assert first["alert_summary"]["open_total"] == 1
    assert len(first["active_alerts"]) == 1

    latest.value_float = 55.0
    latest.save(update_fields=["value_float", "updated_at"])

    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["status", "resolved_at", "updated_at"])

    second = build_data_center_overview_summary(Device.objects.filter(pk=device.pk), cache_scope="test")
    assert second["latest_telemetry"][0]["value_float"] == 55.0
    assert second["alert_summary"]["open_total"] == 0
    assert len(second["active_alerts"]) == 0
