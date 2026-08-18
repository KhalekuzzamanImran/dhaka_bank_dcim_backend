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
