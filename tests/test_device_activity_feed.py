from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest
from rest_framework.test import APIClient
from django.utils import timezone

from apps.accounts.models import User
from apps.alerts.models import AlertEvent, AlertEventLog, AlertEventLogAction, AlertStatus
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import DeviceEvent, DeviceEventSeverity
from apps.traps.models import SNMPTrapEvent, TrapResolutionSource


def _build_device():
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
        name="UPS 01",
        code="UPS-01",
        ip_address="10.10.10.10",
    )
    return org, dc, device


def _aware(value: str):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


@pytest.mark.django_db
def test_device_activity_feed_merges_alert_logs_device_events_and_traps():
    org, dc, device = _build_device()
    alert = AlertEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        severity="WARNING",
        status=AlertStatus.OPEN,
        message="UPS abnormal condition",
        triggered_at=_aware("2026-07-28T10:00:00Z"),
    )
    AlertEventLog.objects.create(
        alert_event=alert,
        action=AlertEventLogAction.OPENED,
        new_status=AlertStatus.OPEN,
        message="Alert opened",
    )
    AlertEventLog.objects.filter(alert_event=alert).update(created_at=_aware("2026-07-28T10:00:00Z"))
    DeviceEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        event_code="UPS_STATUS",
        event_name="UPS Status",
        severity=DeviceEventSeverity.INFO,
        message="Device event",
        occurred_at=_aware("2026-07-28T10:05:00Z"),
    )
    SNMPTrapEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        source_ip=device.ip_address,
        trap_oid="1.3.6.1.4.1.318.0.636",
        event_code="apcTestTrap",
        event_name="apcTestTrap",
        severity="WARNING",
        resolution_source=TrapResolutionSource.MIB,
        mib_module="PowerNet-MIB",
        mib_symbol="apcTestTrap",
        mib_description="Trap used to test SNMP trap functionality.",
        mib_status="CURRENT",
        requires_mapping_review=True,
        raw_varbinds={"_canonical_trap_oid": "1.3.6.1.4.1.318.0.636"},
        message="Trap received",
        received_at=_aware("2026-07-28T10:10:00Z"),
        is_mapped=False,
        is_processed=True,
    )

    client = APIClient()
    user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.force_authenticate(user=user)

    response = client.get(f"/api/v1/devices/devices/{device.pk}/activity/")
    assert response.status_code == 200
    payload = response.json()

    assert payload["active_alarms_count"] == 1
    assert len(payload["active_alarms"]) == 1
    assert payload["active_alarms"][0]["message"] == "UPS abnormal condition"

    assert payload["recent_events_count"] == 3
    assert [item["source_type"] for item in payload["recent_events"]] == [
        "snmp_trap_event",
        "device_event",
        "alert_event_log",
    ]


@pytest.mark.django_db
def test_device_detail_serializer_includes_activity_sections():
    org, dc, device = _build_device()
    AlertEvent.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        severity="WARNING",
        status=AlertStatus.OPEN,
        message="UPS abnormal condition",
        triggered_at=_aware("2026-07-28T10:00:00Z"),
    )

    client = APIClient()
    user = User.objects.create_superuser(username="admin2", password="pass", email="admin2@example.com")
    client.force_authenticate(user=user)

    response = client.get(f"/api/v1/devices/devices/{device.pk}/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["active_alarms_count"] == 1
    assert "recent_events" in payload
