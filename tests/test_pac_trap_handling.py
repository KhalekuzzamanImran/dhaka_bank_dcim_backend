import pytest
from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertStatus
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceCredential, DeviceModel, DeviceProtocolConfig, DeviceType, ProtocolType, SNMPVersion, SNMPOIDMapping, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import DeviceEvent, LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition, TelemetryPoint
from apps.traps.models import SNMPTrapEvent, SNMPTrapOIDMapping, SNMPTrapSource
from collectors.snmp_collector.client import SNMPResult
from collectors.snmp_trap_receiver.pac_alarm_handler import process_pac_trap_alarm_confirmation
from collectors.snmp_trap_receiver.services import process_snmp_trap


def _build_pac_device():
    org = Organization.objects.create(name="Org", code="ORG")
    dc = DataCenter.objects.create(organization=org, name="DC", code="DC-1")
    device_type = DeviceType.objects.create(name="PAC", code="PAC", category="COOLING")
    vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
    model = DeviceModel.objects.create(vendor=vendor, device_type=device_type, name="PAC Model", model_number="PAC-1")
    device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=device_type,
        device_model=model,
        name="PAC-01",
        code="PAC-01",
        ip_address="10.10.10.50",
    )
    DeviceProtocolConfig.objects.create(
        device=device,
        protocol=ProtocolType.SNMP,
        host="10.10.10.50",
        port=161,
        timeout_seconds=5,
        retry_count=1,
        is_primary=True,
        is_enabled=True,
    )
    DeviceCredential.objects.create(
        device=device,
        protocol=ProtocolType.SNMP,
        username="",
        snmp_version=SNMPVersion.V2C,
        snmp_community_encrypted="placeholder",
        is_active=True,
    )
    source = SNMPTrapSource.objects.create(organization=org, data_center=dc, device=device, source_ip="10.10.10.50", is_enabled=True)
    return org, dc, device, source


def _add_pac_alarm_mapping(device):
    metric = MetricDefinition.objects.create(
        code="PAC_GENERAL_ALARM",
        name="PAC General Alarm",
        category=MetricCategory.ALARM,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    SNMPOIDMapping.objects.create(
        device_type=device.device_type,
        device_model=device.device_model,
        vendor=None,
        metric=metric,
        oid="1.3.6.1.4.1.99999.2.1",
        data_type="integer",
        is_active=True,
    )
    trap_mapping = SNMPTrapOIDMapping.objects.create(
        device_type=device.device_type,
        device_model=device.device_model,
        vendor=None,
        trap_oid="1.3.6.1.4.1.99999.1.1",
        event_code="PAC_ALARM_FIRED",
        event_name="PAC Alarm Fired",
        severity="CRITICAL",
        message_template="PAC alarm fired",
        create_alert=True,
        is_active=True,
    )
    return metric, trap_mapping


@pytest.mark.django_db
def test_pac_fired_trap_opens_exact_alert(monkeypatch):
    _, _, device, _ = _build_pac_device()
    metric, trap_mapping = _add_pac_alarm_mapping(device)

    monkeypatch.setattr(
        "collectors.snmp_trap_receiver.tasks.process_pac_trap_alarm_confirmation_task.delay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "collectors.snmp_trap_receiver.pac_alarm_handler.SNMPClient.get",
        lambda self, oid: SNMPResult(oid=oid, value=1, raw_value="1"),
    )

    trap_event = process_snmp_trap(
        source_ip="10.10.10.50",
        trap_oid=trap_mapping.trap_oid,
        raw_varbinds={"1.3.6.1.4.1.99999.1.1": "1"},
    )
    result = process_pac_trap_alarm_confirmation(device_id=str(device.pk), trap_event_id=str(trap_event.pk), event_code="PAC_ALARM_FIRED")

    assert result["processed_count"] == 1
    assert SNMPTrapEvent.objects.count() == 1
    assert TelemetryPoint.objects.count() == 1
    assert LatestTelemetry.objects.count() == 1
    alert = AlertEvent.objects.get(device=device, metric=metric, status=AlertStatus.OPEN)
    assert alert.value_integer == 1
    assert DeviceEvent.objects.filter(device=device, event_code="PAC_ALARM_CONFIRMED_OPEN").exists()


@pytest.mark.django_db
def test_pac_restored_trap_resolves_open_alert(monkeypatch):
    _, _, device, _ = _build_pac_device()
    metric, trap_mapping = _add_pac_alarm_mapping(device)
    AlertEvent.objects.create(
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        metric=metric,
        alert_rule=None,
        severity="CRITICAL",
        status=AlertStatus.OPEN,
        message="PAC alarm confirmed by SNMP poll",
        triggered_at=timezone.now(),
        value_integer=1,
    )

    monkeypatch.setattr(
        "collectors.snmp_trap_receiver.tasks.process_pac_trap_alarm_confirmation_task.delay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "collectors.snmp_trap_receiver.pac_alarm_handler.SNMPClient.get",
        lambda self, oid: SNMPResult(oid=oid, value=0, raw_value="0"),
    )

    trap_event = process_snmp_trap(
        source_ip="10.10.10.50",
        trap_oid=trap_mapping.trap_oid,
        raw_varbinds={"1.3.6.1.4.1.99999.1.1": "0"},
    )
    result = process_pac_trap_alarm_confirmation(device_id=str(device.pk), trap_event_id=str(trap_event.pk), event_code="PAC_ALARM_RESTORED")

    assert result["processed_count"] == 1
    alert = AlertEvent.objects.get(device=device, metric=metric)
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_at is not None
    assert DeviceEvent.objects.filter(device=device, event_code="PAC_ALARM_CONFIRMED_RESOLVED").exists()


@pytest.mark.django_db
def test_duplicate_pac_fired_trap_does_not_create_duplicate_open_alert(monkeypatch):
    _, _, device, _ = _build_pac_device()
    metric, trap_mapping = _add_pac_alarm_mapping(device)

    monkeypatch.setattr(
        "collectors.snmp_trap_receiver.tasks.process_pac_trap_alarm_confirmation_task.delay",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "collectors.snmp_trap_receiver.pac_alarm_handler.SNMPClient.get",
        lambda self, oid: SNMPResult(oid=oid, value=1, raw_value="1"),
    )

    trap_event = process_snmp_trap(
        source_ip="10.10.10.50",
        trap_oid=trap_mapping.trap_oid,
        raw_varbinds={"1.3.6.1.4.1.99999.1.1": "1"},
    )
    process_pac_trap_alarm_confirmation(device_id=str(device.pk), trap_event_id=str(trap_event.pk), event_code="PAC_ALARM_FIRED")
    process_pac_trap_alarm_confirmation(device_id=str(device.pk), trap_event_id=str(trap_event.pk), event_code="PAC_ALARM_FIRED")

    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_non_pac_trap_uses_generic_flow():
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
        ip_address="10.10.10.60",
    )
    SNMPTrapSource.objects.create(organization=org, data_center=dc, device=device, source_ip="10.10.10.60", is_enabled=True)
    metric = MetricDefinition.objects.create(
        code="UPS_ON_BATTERY",
        name="UPS On Battery",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.TEXT,
        is_active=True,
    )
    SNMPOIDMapping.objects.create(
        device_type=device.device_type,
        device_model=device.device_model,
        vendor=None,
        metric=metric,
        oid="1.3.6.1.4.1.99999.2.2",
        data_type="text",
        is_active=True,
    )
    SNMPTrapOIDMapping.objects.create(
        device_type=device.device_type,
        device_model=device.device_model,
        vendor=None,
        trap_oid="1.3.6.1.4.1.99999.1.9",
        event_code="UPS_ON_BATTERY",
        event_name="UPS On Battery",
        severity="WARNING",
        message_template="UPS switched to battery mode",
        create_alert=True,
        is_active=True,
    )

    trap_event = process_snmp_trap(
        source_ip="10.10.10.60",
        trap_oid="1.3.6.1.4.1.99999.1.9",
        raw_varbinds={"1.3.6.1.4.1.99999.1.9": "battery"},
    )

    assert trap_event.is_mapped is True
    assert SNMPTrapEvent.objects.count() == 1
    assert AlertEvent.objects.filter(device=device, metric__isnull=True, status=AlertStatus.OPEN).count() == 1
