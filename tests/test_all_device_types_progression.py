import pytest
from unittest.mock import patch
from django.utils import timezone
from apps.devices.models import Device, DeviceStatus, DeviceType, DevicePollingConfig, PollingProfile, ProtocolType
from apps.organizations.models import Organization
from apps.datacenters.models import DataCenter
from apps.telemetry.models import DeviceEvent
from apps.alerts.models import AlertEvent, AlertStatus
from collectors.snmp_collector.services import _mark_failure as snmp_mark_failure, _mark_success as snmp_mark_success
from collectors.modbus_collector.services import _mark_failure as modbus_mark_failure, _mark_success as modbus_mark_success
from collectors.scheduler.tasks import reconcile_stale_devices


DEVICE_TEST_SPECS = [
    {"code": "RACK_PDU", "name": "Rack PDU", "protocol": ProtocolType.SNMP, "collector": "snmp"},
    {"code": "UPS", "name": "UPS", "protocol": ProtocolType.SNMP, "collector": "snmp"},
    {"code": "PAC", "name": "PAC", "protocol": ProtocolType.MODBUS_TCP, "collector": "modbus"},
    {"code": "ATS", "name": "ATS", "protocol": ProtocolType.SNMP, "collector": "snmp"},
    {"code": "NETBOTZ", "name": "NetBotz", "protocol": ProtocolType.SNMP, "collector": "snmp"},
]


@pytest.mark.django_db
@pytest.mark.parametrize("spec", DEVICE_TEST_SPECS)
def test_all_device_types_progression_and_recovery(spec):
    """
    Verifies that for every device type without physically touching hardware:
    1. 1st failure (<120s): Stays ONLINE (jitter buffer)
    2. 2nd failure (120s): DEGRADED + logs DEVICE_DEGRADED
    3. 3rd failure (180s): OFFLINE + logs DEVICE_OFFLINE + opens AlertEvent
    4. Recovery: ONLINE + logs DEVICE_ONLINE + auto-resolves AlertEvent
    """
    org = Organization.objects.create(name=f"Org {spec['code']}", code=f"ORG-{spec['code']}")
    dc = DataCenter.objects.create(name=f"DC {spec['code']}", code=f"DC-{spec['code']}", organization=org)
    dtype = DeviceType.objects.create(name=spec["name"], code=spec["code"])
    now = timezone.now()

    device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=dtype,
        name=f"Test {spec['name']}",
        code=f"DEV-{spec['code']}-01",
        status=DeviceStatus.ONLINE,
        last_seen_at=now,
    )
    profile = PollingProfile.objects.create(
        name=f"Profile {spec['code']}",
        protocol=spec["protocol"],
        interval_seconds=60,
        stale_after_seconds=180,
    )
    pconfig = DevicePollingConfig.objects.create(
        device=device,
        polling_profile=profile,
        consecutive_failures=0,
    )

    mark_failure = snmp_mark_failure if spec["collector"] == "snmp" else modbus_mark_failure
    mark_success = snmp_mark_success if spec["collector"] == "snmp" else modbus_mark_success

    # Step 1: 1st failure (T = 60s)
    t1 = now + timezone.timedelta(seconds=60)
    mark_failure(device, pconfig, "timeout 1", t1)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.ONLINE
    assert pconfig.consecutive_failures == 1

    # Step 2: 2nd failure (T = 120s) -> DEGRADED
    t2 = now + timezone.timedelta(seconds=120)
    mark_failure(device, pconfig, "timeout 2", t2)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.DEGRADED
    assert pconfig.consecutive_failures == 2
    assert DeviceEvent.objects.filter(device=device, event_code="DEVICE_DEGRADED").exists()

    # Step 3: 3rd failure (T = 180s) -> OFFLINE
    t3 = now + timezone.timedelta(seconds=180)
    mark_failure(device, pconfig, "timeout 3", t3)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.OFFLINE
    assert pconfig.consecutive_failures == 3
    assert DeviceEvent.objects.filter(device=device, event_code="DEVICE_OFFLINE").exists()

    # Check AlertEvent was raised
    alert = AlertEvent.objects.filter(device=device, status=AlertStatus.OPEN).first()
    assert alert is not None
    assert "offline" in alert.message.lower()

    # Step 4: Recovery (T = 240s) -> ONLINE
    t4 = now + timezone.timedelta(seconds=240)
    mark_success(device, pconfig, t4)
    device.refresh_from_db()
    pconfig.refresh_from_db()
    assert device.status == DeviceStatus.ONLINE
    assert pconfig.consecutive_failures == 0
    assert DeviceEvent.objects.filter(device=device, event_code="DEVICE_ONLINE").exists()

    # AlertEvent auto-resolved
    alert.refresh_from_db()
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_at == t4
    assert alert.resolution_type == "AUTO"

    # Verify total events recorded: DEGRADED, OFFLINE, ONLINE
    events = list(DeviceEvent.objects.filter(device=device).order_by("occurred_at").values_list("event_code", flat=True))
    assert events == ["DEVICE_DEGRADED", "DEVICE_OFFLINE", "DEVICE_ONLINE"]
