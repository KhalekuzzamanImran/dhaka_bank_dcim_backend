from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import AlertComment, AlertEvent, AlertEventLog, AlertEventLogAction, AlertRule, AlertSeverity, AlertStatus
from apps.alerts.services import acknowledge_alert, evaluate_latest, manually_resolve_alert
from apps.alerts.services import engine as alert_engine
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition
from apps.traps.models import SNMPTrapOIDMapping, SNMPTrapSource
from collectors.snmp_trap_receiver.services import process_snmp_trap
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.notifications.services import deliver_pending_notifications


def _device_setup():
    org = Organization.objects.create(name="Org", code="ORG")
    dc = DataCenter.objects.create(organization=org, name="DC", code="DC")
    device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
    vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
    model = DeviceModel.objects.create(vendor=vendor, device_type=device_type, name="Model", model_number="M1")
    device = Device.objects.create(organization=org, data_center=dc, device_type=device_type, device_model=model, name="UPS-01", code="UPS-01")
    metric = MetricDefinition.objects.create(code="UPS_ON_BATTERY", name="UPS On Battery", category=MetricCategory.STATUS, data_type=MetricDataType.INTEGER, is_active=True)
    return org, dc, device, metric


def _rule(device, metric, threshold=1, duration_seconds=0):
    return AlertRule.objects.create(
        organization=device.organization,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name="UPS On Battery",
        operator="EQ",
        threshold_integer=threshold,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=duration_seconds,
        is_active=True,
    )


def _latest(device, metric, value):
    return LatestTelemetry.objects.create(
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        metric=metric,
        value_integer=value,
        quality="GOOD",
        last_seen_at=timezone.now(),
        source="test",
    )


@pytest.mark.django_db
def test_alert_opens_and_does_not_duplicate():
    _, _, device, metric = _device_setup()
    _rule(device, metric)
    latest = _latest(device, metric, 1)

    evaluate_latest(latest)
    evaluate_latest(latest)

    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1
    assert AlertEventLog.objects.filter(action=AlertEventLogAction.OPENED).count() == 1
    assert AlertEventLog.objects.filter(action=AlertEventLogAction.UPDATED).count() >= 1


@pytest.mark.django_db
def test_alert_auto_resolves_when_condition_clears():
    _, _, device, metric = _device_setup()
    _rule(device, metric)
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    latest.value_integer = 0
    latest.save(update_fields=["value_integer", "updated_at"])

    evaluate_latest(latest)

    alert = AlertEvent.objects.get(device=device, metric=metric)
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_at is not None
    assert AlertEventLog.objects.filter(action=AlertEventLogAction.RESOLVED).exists()


@pytest.mark.django_db
def test_threshold_zero_is_respected():
    _, _, device, metric = _device_setup()
    AlertRule.objects.create(
        organization=device.organization,
        device=device,
        metric=metric,
        name="Zero threshold",
        operator="GTE",
        threshold_integer=0,
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 0)
    evaluate_latest(latest)
    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_acknowledge_and_resolve_adds_comments():
    _, _, device, metric = _device_setup()
    _rule(device, metric)
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    alert = AlertEvent.objects.get(device=device, metric=metric)

    acknowledge_alert(alert, user=None, comment="Checking device")
    alert.refresh_from_db()
    assert alert.status == AlertStatus.ACKNOWLEDGED
    assert AlertComment.objects.filter(alert_event=alert).count() == 1

    manually_resolve_alert(alert, user=None, comment="Issue cleared")
    alert.refresh_from_db()
    assert alert.status == AlertStatus.RESOLVED
    assert AlertComment.objects.filter(alert_event=alert).count() == 2


@pytest.mark.django_db
def test_duration_prevents_early_open(monkeypatch):
    _, _, device, metric = _device_setup()
    _rule(device, metric, duration_seconds=60)
    latest = _latest(device, metric, 1)

    frozen = timezone.now()
    monkeypatch.setattr(alert_engine.timezone, "now", lambda: frozen)
    evaluate_latest(latest)
    assert AlertEvent.objects.filter(device=device, metric=metric).count() == 0

    monkeypatch.setattr(alert_engine.timezone, "now", lambda: frozen + timedelta(seconds=61))
    evaluate_latest(latest)
    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_generic_trap_creates_single_durable_alert():
    org, dc, device, _ = _device_setup()
    SNMPTrapSource.objects.create(organization=org, data_center=dc, device=device, source_ip="10.10.10.50", is_enabled=True)
    SNMPTrapOIDMapping.objects.create(
        device_type=device.device_type,
        device_model=device.device_model,
        vendor=None,
        trap_oid="1.3.6.1.4.1.99999.1.9",
        event_code="UPS_ON_BATTERY",
        event_name="UPS On Battery",
        severity="WARNING",
        message_template="UPS on battery",
        create_alert=True,
        is_active=True,
    )

    process_snmp_trap(source_ip="10.10.10.50", trap_oid="1.3.6.1.4.1.99999.1.9", raw_varbinds={"x": "y"})
    process_snmp_trap(source_ip="10.10.10.50", trap_oid="1.3.6.1.4.1.99999.1.9", raw_varbinds={"x": "y"})

    assert AlertEvent.objects.filter(device=device, metric__isnull=True, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_pending_web_notifications_are_delivered():
    org, _, _, _ = _device_setup()
    notification = Notification.objects.create(
        organization=org,
        recipient=None,
        channel=NotificationChannel.WEB,
        subject="Test",
        message="Hello",
        status=NotificationStatus.PENDING,
    )

    deliver_pending_notifications()
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.SENT
