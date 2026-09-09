import pytest
from django.utils import timezone
from apps.devices.models import Device, DeviceStatus, DeviceType, DevicePollingConfig, PollingProfile, ProtocolType
from apps.organizations.models import Organization
from apps.datacenters.models import DataCenter
from apps.telemetry.models import DeviceEvent
from apps.alerts.models import AlertEvent, AlertStatus
from collectors.snmp_collector.services import _mark_failure, _mark_success
from collectors.scheduler.tasks import reconcile_stale_devices


@pytest.mark.django_db
def test_device_status_lifecycle_and_outage_tracking():
    org = Organization.objects.create(name="Test Org", code="TEST-ORG")
    dc = DataCenter.objects.create(name="DC 1", code="DC-1", organization=org)
    dtype = DeviceType.objects.create(name="Rack PDU", code="RACK_PDU")
    now = timezone.now()

    device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=dtype,
        name="Rack PDU Test",
        code="PDU-TEST-01",
        status=DeviceStatus.ONLINE,
        last_seen_at=now,
    )
    profile = PollingProfile.objects.create(
        name="Standard PDU",
        protocol=ProtocolType.SNMP,
        interval_seconds=60,
        stale_after_seconds=180,
    )
    pconfig = DevicePollingConfig.objects.create(
        device=device,
        polling_profile=profile,
        consecutive_failures=0,
    )

    # 1. First poll failure (T = 60s, failure_count = 1) -> Remains ONLINE to tolerate jitter
    t1 = now + timezone.timedelta(seconds=60)
    _mark_failure(device, pconfig, "timeout 1", t1)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.ONLINE
    assert pconfig.consecutive_failures == 1

    # 2. Second poll failure (T = 120s, failure_count = 2) -> Transitions to DEGRADED
    t2 = now + timezone.timedelta(seconds=120)
    _mark_failure(device, pconfig, "timeout 2", t2)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.DEGRADED
    assert pconfig.consecutive_failures == 2
    assert DeviceEvent.objects.filter(device=device, event_code="DEVICE_DEGRADED").exists()

    # 3. Third poll failure (T = 180s, failure_count = 3) -> Transitions to OFFLINE
    t3 = now + timezone.timedelta(seconds=180)
    _mark_failure(device, pconfig, "timeout 3", t3)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.OFFLINE
    assert pconfig.consecutive_failures == 3
    assert DeviceEvent.objects.filter(device=device, event_code="DEVICE_OFFLINE").exists()

    # Verify AlertEvent was opened for offline
    open_alert = AlertEvent.objects.filter(device=device, status=AlertStatus.OPEN).first()
    assert open_alert is not None
    assert "offline" in open_alert.message.lower()
    assert open_alert.triggered_at == t3

    # 4. Device recovers (T = 300s, poll succeeds) -> Recovers to ONLINE, resolves AlertEvent
    t4 = now + timezone.timedelta(seconds=300)
    _mark_success(device, pconfig, t4)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.ONLINE
    assert pconfig.consecutive_failures == 0
    assert DeviceEvent.objects.filter(device=device, event_code="DEVICE_ONLINE").exists()

    # Verify AlertEvent was automatically resolved with resolved_at
    open_alert.refresh_from_db()
    assert open_alert.status == AlertStatus.RESOLVED
    assert open_alert.resolved_at == t4
    assert open_alert.resolution_type == "AUTO"


@pytest.mark.django_db
def test_scheduler_reconciliation_degraded_and_offline():
    org = Organization.objects.create(name="Test Org 2", code="TEST-ORG-2")
    dc = DataCenter.objects.create(name="DC 2", code="DC-2", organization=org)
    dtype = DeviceType.objects.create(name="Rack PDU 2", code="RACK_PDU_2")
    now = timezone.now()

    profile = PollingProfile.objects.create(
        name="Standard PDU 2",
        protocol=ProtocolType.SNMP,
        interval_seconds=60,
        stale_after_seconds=180,
    )

    # Device A: 130s since last seen -> Should become DEGRADED
    dev_a = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=dtype,
        name="Device Degraded Test",
        code="DEV-DEG-01",
        status=DeviceStatus.ONLINE,
        last_seen_at=now - timezone.timedelta(seconds=130),
    )
    DevicePollingConfig.objects.create(device=dev_a, polling_profile=profile)

    # Device B: 200s since last seen -> Should become OFFLINE
    dev_b = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=dtype,
        name="Device Offline Test",
        code="DEV-OFF-01",
        status=DeviceStatus.ONLINE,
        last_seen_at=now - timezone.timedelta(seconds=200),
    )
    DevicePollingConfig.objects.create(device=dev_b, polling_profile=profile)

    result = reconcile_stale_devices()
    dev_a.refresh_from_db()
    dev_b.refresh_from_db()

    assert dev_a.status == DeviceStatus.DEGRADED
    assert dev_b.status == DeviceStatus.OFFLINE
