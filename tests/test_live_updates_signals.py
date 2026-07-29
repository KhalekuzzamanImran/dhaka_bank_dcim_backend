import pytest
from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertEventLog, AlertSeverity, AlertStatus
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.live_updates import signals
from apps.organizations.models import Organization
from apps.telemetry.models import DeviceEvent, DeviceEventSeverity
from apps.traps.models import SNMPTrapEvent


def _build_device(*, device_type_code: str, name: str):
    org = Organization.objects.create(name="Org", code=f"{device_type_code}-ORG")
    dc = DataCenter.objects.create(organization=org, name="DC", code=f"{device_type_code}-DC")
    device_type = DeviceType.objects.create(name=device_type_code, code=device_type_code, category="POWER")
    vendor = Vendor.objects.create(name="Vendor", code=f"{device_type_code}-VENDOR")
    model = DeviceModel.objects.create(
        vendor=vendor,
        device_type=device_type,
        name=f"{device_type_code} Model",
        model_number=f"{device_type_code}-1",
    )
    device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=device_type,
        device_model=model,
        name=name,
        code=name,
        ip_address="10.10.10.10",
    )
    return org, dc, device


@pytest.mark.django_db
def test_live_updates_only_publish_ups_events(monkeypatch):
    published = []

    monkeypatch.setattr(
        "apps.live_updates.signals.publish_live_update",
        lambda **kwargs: published.append(kwargs) or kwargs,
    )

    _, _, ups_device = _build_device(device_type_code="UPS", name="UPS-01")
    _, _, pac_device = _build_device(device_type_code="PAC", name="PAC-01")

    assert [event["event_type"] for event in published[:2]] == ["device_update", "device_update"]
    published.clear()

    alert_event = AlertEvent.objects.create(
        organization=ups_device.organization,
        data_center=ups_device.data_center,
        device=ups_device,
        metric=None,
        alert_rule=None,
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.OPEN,
        message="UPS on battery",
        triggered_at=timezone.now(),
    )
    AlertEventLog.objects.create(
        alert_event=alert_event,
        action="OPENED",
    )
    DeviceEvent.objects.create(
        organization=ups_device.organization,
        data_center=ups_device.data_center,
        device=ups_device,
        event_code="UPS_ON_BATTERY",
        event_name="UPS On Battery",
        severity=DeviceEventSeverity.WARNING,
        message="UPS switched to battery backup",
        occurred_at=timezone.now(),
    )
    SNMPTrapEvent.objects.create(
        organization=ups_device.organization,
        data_center=ups_device.data_center,
        device=ups_device,
        source_ip="10.10.10.10",
        trap_oid="1.3.6.1.4.1.318.0.9",
        event_code="powerRestored",
        event_name="Power Restored",
        severity="WARNING",
        received_at=timezone.now(),
    )

    AlertEvent.objects.create(
        organization=pac_device.organization,
        data_center=pac_device.data_center,
        device=pac_device,
        metric=None,
        alert_rule=None,
        severity=AlertSeverity.WARNING,
        status=AlertStatus.OPEN,
        message="PAC alert",
        triggered_at=timezone.now(),
    )
    DeviceEvent.objects.create(
        organization=pac_device.organization,
        data_center=pac_device.data_center,
        device=pac_device,
        event_code="PAC_EVENT",
        event_name="PAC Event",
        severity=DeviceEventSeverity.INFO,
        message="PAC event",
        occurred_at=timezone.now(),
    )
    SNMPTrapEvent.objects.create(
        organization=pac_device.organization,
        data_center=pac_device.data_center,
        device=pac_device,
        source_ip="10.10.10.11",
        trap_oid="1.3.6.1.4.1.99999.1.1",
        event_code="PAC_EVENT",
        event_name="PAC Event",
        severity="INFO",
        received_at=timezone.now(),
    )

    assert [event["event_type"] for event in published] == [
        "alert_event",
        "alert_event_log",
        "device_event",
        "snmp_trap_event",
    ]
    assert all(event["metadata"]["device_id"] == str(ups_device.pk) for event in published if "device_id" in event["metadata"])
    assert all(event["metadata"].get("alert_event_id") == str(alert_event.pk) for event in published if "alert_event_id" in event["metadata"])
