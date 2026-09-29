from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.live_updates.services import device_scopes, publish_device_status_update


DEVICE_TYPES = ("UPS", "ATS", "PAC", "RACK_PDU", "NETBOTZ")


def _build_device(device_type_code: str):
    device_id = uuid4()
    organization_id = uuid4()
    data_center_id = uuid4()
    last_seen_at = timezone.now()
    return SimpleNamespace(
        pk=device_id,
        organization_id=organization_id,
        data_center_id=data_center_id,
        device_type=SimpleNamespace(code=device_type_code),
        last_seen_at=last_seen_at,
        status="ONLINE",
    )


@pytest.mark.parametrize("device_type_code", DEVICE_TYPES)
def test_device_scopes_include_every_authorized_device_family(device_type_code):
    device = _build_device(device_type_code)

    assert device_scopes(device) == [
        "overview",
        f"device:{device.pk}",
        f"organization:{device.organization_id}",
        f"data_center:{device.data_center_id}",
        f"device_type:{device_type_code}",
    ]


@pytest.mark.parametrize("device_type_code", DEVICE_TYPES)
def test_publish_device_status_update_uses_synthetic_offline_payload_without_mutating_state(monkeypatch, device_type_code):
    device = _build_device(device_type_code)
    published = []

    monkeypatch.setattr(
        "apps.live_updates.services.publish_live_update",
        lambda **kwargs: published.append(kwargs) or kwargs,
    )

    event = publish_device_status_update(
        device,
        status="offline",
        previous_status="online",
        reason="stale heartbeat",
        source="synthetic-test",
    )

    assert event == published[-1]
    assert event["event_type"] == "device_status_changed"
    assert event["resource_type"] == "Device"
    assert event["resource_id"] == device.pk
    assert event["scopes"] == [
        "overview",
        f"device:{device.pk}",
        f"organization:{device.organization_id}",
        f"data_center:{device.data_center_id}",
        f"device_type:{device_type_code}",
    ]

    metadata = event["metadata"]
    assert metadata["device_id"] == str(device.pk)
    assert metadata["organization_id"] == str(device.organization_id)
    assert metadata["data_center_id"] == str(device.data_center_id)
    assert metadata["device_type"] == device_type_code
    assert metadata["status"] == "OFFLINE"
    assert metadata["previous_status"] == "ONLINE"
    assert metadata["reason"] == "stale heartbeat"
    assert metadata["source"] == "synthetic-test"
    assert metadata["last_seen_at"] == device.last_seen_at.isoformat()
    assert metadata["observed_at"] is None
    assert metadata["updated_at"] is not None


@pytest.mark.parametrize("device_type_code", DEVICE_TYPES)
def test_publish_device_status_update_online_uses_observed_at_as_last_seen(monkeypatch, device_type_code):
    device = _build_device(device_type_code)
    observed_at = timezone.now()
    published = []

    monkeypatch.setattr(
        "apps.live_updates.services.publish_live_update",
        lambda **kwargs: published.append(kwargs) or kwargs,
    )

    event = publish_device_status_update(
        device,
        status="online",
        previous_status="offline",
        reason="poll recovered",
        source="synthetic-test",
        observed_at=observed_at,
    )

    assert event == published[-1]
    metadata = event["metadata"]
    assert metadata["status"] == "ONLINE"
    assert metadata["previous_status"] == "OFFLINE"
    assert metadata["observed_at"] == observed_at.isoformat()
    assert metadata["last_seen_at"] == observed_at.isoformat()
    assert metadata["updated_at"] == observed_at.isoformat()


def test_publish_live_update_broadcasts_to_global_and_scoped_audiences(monkeypatch):
    from apps.live_updates.services import live_update_group_name, publish_live_update

    sent_groups = []

    class DummyChannelLayer:
        async def group_send(self, group, message):
            sent_groups.append((group, message))

    monkeypatch.setattr("apps.live_updates.services.get_channel_layer", lambda: DummyChannelLayer())

    publish_live_update(
        event_type="device_status_changed",
        resource_type="Device",
        resource_id="11111111-1111-1111-1111-111111111111",
        scopes=["overview", "device:11111111-1111-1111-1111-111111111111", "organization:22222222-2222-2222-2222-222222222222", "device_type:UPS"],
        metadata={"status": "OFFLINE"},
    )

    delivered_group_names = {group for group, _ in sent_groups}
    assert live_update_group_name("global") in delivered_group_names
    assert live_update_group_name("device:11111111-1111-1111-1111-111111111111") in delivered_group_names
    assert live_update_group_name("organization:22222222-2222-2222-2222-222222222222") in delivered_group_names


@pytest.mark.django_db
def test_publish_device_status_update_creates_alert_event_and_notifications_on_offline(monkeypatch):
    from apps.organizations.models import Organization
    from apps.datacenters.models import DataCenter
    from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
    from apps.accounts.models import User
    from apps.alerts.models import AlertEvent, AlertEventLog, AlertEventLogAction, AlertStatus, AlertSeverity
    from apps.notifications.models import Notification, NotificationDelivery, NotificationChannel, NotificationStatus

    org = Organization.objects.create(name="Test Org", code="TORG")
    dc = DataCenter.objects.create(organization=org, name="Test DC", code="TDC")
    dtype = DeviceType.objects.create(name="PAC", code="PAC", category="COOLING")
    vendor = Vendor.objects.create(name="Schneider", code="SCH")
    model = DeviceModel.objects.create(vendor=vendor, device_type=dtype, name="Uniflair", model_number="U40")
    device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=dtype,
        device_model=model,
        name="PAC 03",
        code="PAC-03",
        ip_address="172.25.210.125",
        status="ONLINE",
    )

    admin_user = User.objects.create_superuser(
        username="admin_test",
        email="admin_test@example.com",
        password="password123",
    )
    admin_user.phone = "01329665857"
    admin_user.save()

    monkeypatch.setattr(
        "apps.alerts.services.notifications._queue_delivery",
        lambda delivery: delivery,
    )

    # 1. Device transitions to OFFLINE
    publish_device_status_update(
        device,
        status="OFFLINE",
        previous_status="ONLINE",
        reason="connectivity lost",
        source="snmp_worker",
    )

    # Verify AlertEvent created
    alert = AlertEvent.objects.filter(device=device, status=AlertStatus.OPEN).first()
    assert alert is not None
    assert alert.severity == AlertSeverity.CRITICAL
    assert "offline" in alert.message.lower()
    assert alert.metadata.get("alert_type") == "DEVICE_OFFLINE"

    # Verify AlertEventLog created
    open_log = AlertEventLog.objects.filter(alert_event=alert, action=AlertEventLogAction.OPENED).first()
    assert open_log is not None
    assert open_log.new_status == AlertStatus.OPEN

    # Verify Notification created
    notif = Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="OPENED").first()
    assert notif is not None
    assert notif.recipient == admin_user
    assert "PAC 03" in notif.subject

    # Verify NotificationDeliveries created (WEB, EMAIL, SMS)
    deliveries = list(NotificationDelivery.objects.filter(notification=notif))
    channels = sorted(d.channel for d in deliveries)
    assert channels == [NotificationChannel.EMAIL, NotificationChannel.SMS, NotificationChannel.WEB]

    # Verify WEB delivery status
    web_delivery = next(d for d in deliveries if d.channel == NotificationChannel.WEB)
    assert web_delivery.status == NotificationStatus.SENT

    # Verify idempotency: calling OFFLINE again does not duplicate alert
    publish_device_status_update(
        device,
        status="OFFLINE",
        previous_status="OFFLINE",
        reason="connectivity lost",
        source="snmp_worker",
    )
    assert AlertEvent.objects.filter(device=device, status=AlertStatus.OPEN).count() == 1

    # 2. Device recovers to ONLINE
    publish_device_status_update(
        device,
        status="ONLINE",
        previous_status="OFFLINE",
        reason="poll success",
        source="snmp_worker",
    )

    # Verify AlertEvent auto-resolved
    alert.refresh_from_db()
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolution_type == "AUTO"
    assert alert.resolved_at is not None

    # Verify AlertEventLog RESOLVED created
    resolve_log = AlertEventLog.objects.filter(alert_event=alert, action=AlertEventLogAction.RESOLVED).first()
    assert resolve_log is not None
    assert resolve_log.new_status == AlertStatus.RESOLVED

    # Verify Notification RESOLVED created (WEB, EMAIL)
    resolve_notif = Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="RESOLVED").first()
    assert resolve_notif is not None
    resolve_deliveries = list(NotificationDelivery.objects.filter(notification=resolve_notif))
    resolve_channels = sorted(d.channel for d in resolve_deliveries)
    assert resolve_channels == [NotificationChannel.EMAIL, NotificationChannel.WEB]

