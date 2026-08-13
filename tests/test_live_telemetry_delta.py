from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.live_updates.services import telemetry_delta_from_latest
from apps.live_updates.consumers import LiveUpdatesConsumer
from apps.live_updates.services import live_update_group_name
from apps.access_control.models import RoleScope


def test_telemetry_delta_preserves_typed_value_and_scope_metadata():
    observed_at = SimpleNamespace(isoformat=lambda: "2026-08-12T10:00:00+00:00")
    last_seen_at = SimpleNamespace(isoformat=lambda: "2026-08-12T10:00:00+00:00")
    latest = SimpleNamespace(
        device_id="device-1",
        organization_id="org-1",
        data_center_id="dc-1",
        device=SimpleNamespace(pk="device-1", device_type=SimpleNamespace(code="UPS")),
        metric=SimpleNamespace(code="ups_load_percent"),
        value_float=42.5,
        value_integer=None,
        value_boolean=None,
        value_text=None,
        quality="GOOD",
        last_seen_at=last_seen_at,
        source="snmp_worker",
    )

    assert telemetry_delta_from_latest(latest, observed_at=observed_at) == {
        "device_id": "device-1",
        "organization_id": "org-1",
        "data_center_id": "dc-1",
        "device_type": "UPS",
        "metric_code": "ups_load_percent",
        "value_float": 42.5,
        "value_integer": None,
        "value_boolean": None,
        "value_text": None,
        "quality": "GOOD",
        "observed_at": "2026-08-12T10:00:00+00:00",
        "last_seen_at": "2026-08-12T10:00:00+00:00",
        "source": "snmp_worker",
    }


@pytest.mark.django_db
def test_scoped_user_groups_include_only_authorized_resources(monkeypatch):
    organization_id = uuid4()
    data_center_id = uuid4()
    device_id = uuid4()
    monkeypatch.setattr("apps.live_updates.consumers.get_access_scope", lambda user: {"global_access": False})
    monkeypatch.setattr("apps.live_updates.consumers.get_user_resource_access_rows", lambda user: [
        SimpleNamespace(role=SimpleNamespace(scope=RoleScope.ORGANIZATION), organization_id=organization_id),
        SimpleNamespace(role=SimpleNamespace(scope=RoleScope.DATA_CENTER), data_center_id=data_center_id),
        SimpleNamespace(role=SimpleNamespace(scope=RoleScope.DEVICE), device_id=device_id),
    ])

    groups = LiveUpdatesConsumer._get_groups_for_user(object())

    assert set(groups) == {
        live_update_group_name(f"organization:{organization_id}"),
        live_update_group_name(f"data_center:{data_center_id}"),
        live_update_group_name(f"device:{device_id}"),
    }
    assert live_update_group_name("organization:org-b") not in groups


def test_global_access_is_reserved_for_global_users(monkeypatch):
    monkeypatch.setattr(
        "apps.live_updates.consumers.get_access_scope",
        lambda user: {
            "global_access": True,
            "organization_ids": set(),
            "data_center_ids": set(),
            "device_ids": set(),
        },
    )

    assert LiveUpdatesConsumer._get_groups_for_user(object()) == [live_update_group_name("global")]
