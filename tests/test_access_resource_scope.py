import pytest
from rest_framework.test import APIClient
from django.core.exceptions import ValidationError

from apps.access_control.models import Permission, Role, RoleScope, RolePermission, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter, Rack, Room
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization


def _json_results(response):
    payload = response.json()
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


def _perm(code):
    return Permission.objects.get_or_create(
        code=code,
        defaults={"module": code.split(".")[0], "description": code},
    )[0]


def _role(code, name, scope, perm_codes):
    role, _ = Role.objects.update_or_create(
        code=code,
        defaults={"name": name, "scope": scope, "status": "ACTIVE"},
    )
    for perm_code in perm_codes:
        RolePermission.objects.get_or_create(role=role, permission=_perm(perm_code))
    return role


def _device(org, dc, room, rack, device_type, code, name):
    model, _ = DeviceModel.objects.get_or_create(
        vendor=Vendor.objects.get_or_create(code="GENERIC", defaults={"name": "Generic"})[0],
        device_type=device_type,
        model_number=f"{device_type.code}-MODEL",
        defaults={"name": f"{device_type.name} Model"},
    )
    return Device.objects.create(
        organization=org,
        data_center=dc,
        room=room,
        rack=rack,
        device_type=device_type,
        device_model=model,
        code=code,
        name=name,
        ip_address=f"10.10.10.{Device.objects.count() + 10}",
        status="UNKNOWN",
    )


def _make_access(user, role, *, organization=None, data_center=None, room=None, rack=None, device=None):
    return UserResourceAccess.objects.create(
        user=user,
        role=role,
        organization=organization,
        data_center=data_center,
        room=room,
        rack=rack,
        device=device,
        assigned_by=user,
        is_active=True,
    )


@pytest.fixture
def hierarchy():
    org = Organization.objects.create(name="Dhaka Bank", code="DHAKA_BANK")
    dc1 = DataCenter.objects.create(organization=org, name="Primary Data Center", code="PDC")
    dc2 = DataCenter.objects.create(organization=org, name="Backup Data Center", code="BDC")
    room1 = Room.objects.create(data_center=dc1, name="Room 1", code="R1")
    room2 = Room.objects.create(data_center=dc1, name="Room 2", code="R2")
    room3 = Room.objects.create(data_center=dc2, name="Room 3", code="R3")
    rack1 = Rack.objects.create(data_center=dc1, room=room1, name="Rack 1", code="RK1")
    rack2 = Rack.objects.create(data_center=dc1, room=room2, name="Rack 2", code="RK2")
    rack3 = Rack.objects.create(data_center=dc2, room=room3, name="Rack 3", code="RK3")
    ups = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
    pac = DeviceType.objects.create(name="PAC", code="PAC", category="COOLING")
    ats = DeviceType.objects.create(name="ATS", code="ATS", category="POWER")
    d1 = _device(org, dc1, room1, rack1, ups, "UPS-01", "UPS 01")
    d2 = _device(org, dc1, room1, rack1, pac, "PAC-01", "PAC 01")
    d3 = _device(org, dc1, room2, rack2, ups, "UPS-02", "UPS 02")
    d4 = _device(org, dc2, room3, rack3, ats, "ATS-01", "ATS 01")
    return {
        "org": org,
        "dc1": dc1,
        "dc2": dc2,
        "room1": room1,
        "room2": room2,
        "room3": room3,
        "rack1": rack1,
        "rack2": rack2,
        "rack3": rack3,
        "ups": ups,
        "pac": pac,
        "ats": ats,
        "d1": d1,
        "d2": d2,
        "d3": d3,
        "d4": d4,
    }


@pytest.fixture
def roles():
    return {
        "bank": _role("BANK_ADMIN", "Bank Admin", RoleScope.ORGANIZATION, ["device.view", "organization.view", "access.view", "access.manage"]),
        "dc": _role("DATA_CENTER_ADMIN", "Data Center Admin", RoleScope.DATA_CENTER, ["device.view", "organization.view"]),
        "room": _role("ROOM_ADMIN", "Room Admin", RoleScope.ROOM, ["device.view"]),
        "rack": _role("RACK_ADMIN", "Rack Admin", RoleScope.RACK, ["device.view"]),
        "device": _role("DEVICE_ADMIN", "Device Admin", RoleScope.DEVICE, ["device.view"]),
        "global": _role("SUPER_ADMIN", "Super Admin", RoleScope.GLOBAL, ["device.view", "organization.view", "access.view"]),
    }


@pytest.mark.django_db
def test_bank_admin_sees_all_devices_under_organization(hierarchy, roles):
    user = User.objects.create_user(username="bank_user", password="pass")
    _make_access(user, roles["bank"], organization=hierarchy["org"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/")
    assert response.status_code == 200
    codes = {item["code"] for item in _json_results(response)}
    assert codes == {"UPS-01", "PAC-01", "UPS-02", "ATS-01"}


@pytest.mark.django_db
def test_data_center_admin_sees_only_assigned_data_center_devices(hierarchy, roles):
    user = User.objects.create_user(username="dc_user", password="pass")
    _make_access(user, roles["dc"], organization=hierarchy["org"], data_center=hierarchy["dc1"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/")
    assert response.status_code == 200
    codes = {item["code"] for item in _json_results(response)}
    assert codes == {"UPS-01", "PAC-01", "UPS-02"}


@pytest.mark.django_db
def test_room_admin_sees_only_assigned_room_devices(hierarchy, roles):
    user = User.objects.create_user(username="room_user", password="pass")
    _make_access(user, roles["room"], organization=hierarchy["org"], data_center=hierarchy["dc1"], room=hierarchy["room1"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/")
    assert response.status_code == 200
    codes = {item["code"] for item in _json_results(response)}
    assert codes == {"UPS-01", "PAC-01"}


@pytest.mark.django_db
def test_rack_admin_sees_only_assigned_rack_devices(hierarchy, roles):
    user = User.objects.create_user(username="rack_user", password="pass")
    _make_access(
        user,
        roles["rack"],
        organization=hierarchy["org"],
        data_center=hierarchy["dc1"],
        room=hierarchy["room1"],
        rack=hierarchy["rack1"],
    )

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/")
    assert response.status_code == 200
    codes = {item["code"] for item in _json_results(response)}
    assert codes == {"UPS-01", "PAC-01"}


@pytest.mark.django_db
def test_device_admin_sees_only_selected_device(hierarchy, roles):
    user = User.objects.create_user(username="device_user", password="pass")
    _make_access(user, roles["device"], device=hierarchy["d1"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/")
    assert response.status_code == 200
    results = _json_results(response)
    assert [item["code"] for item in results] == ["UPS-01"]


@pytest.mark.django_db
def test_user_with_no_access_rows_sees_no_access_context_or_sidebar_data():
    user = User.objects.create_user(username="no_access", password="pass")
    client = APIClient()
    client.force_authenticate(user=user)

    access_context = client.get("/api/v1/access/user-resource-accesses/access_context/")
    assert access_context.status_code == 200
    payload = access_context.json()
    assert payload["organizations"] == []
    assert payload["data_centers"] == []
    assert payload["rooms"] == []
    assert payload["racks"] == []
    assert payload["devices"] == []

    sidebar = client.get("/api/v1/devices/device-types/available_for_me/")
    assert sidebar.status_code == 200
    assert sidebar.json() == []


@pytest.mark.django_db
def test_global_access_and_available_device_types(hierarchy, roles):
    user = User.objects.create_user(username="global_user", password="pass")
    _make_access(user, roles["global"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/devices/devices/")
    assert response.status_code == 200
    assert {item["code"] for item in _json_results(response)} == {"UPS-01", "PAC-01", "UPS-02", "ATS-01"}

    sidebar = client.get("/api/v1/devices/device-types/available_for_me/")
    assert sidebar.status_code == 200
    assert {item["code"]: item["device_count"] for item in sidebar.json()} == {"ATS": 1, "PAC": 1, "UPS": 2}


@pytest.mark.django_db
def test_bank_admin_blank_scope_is_invalid(hierarchy, roles):
    with pytest.raises(ValidationError):
        UserResourceAccess.objects.create(user=User.objects.create_user(username="invalid", password="pass"), role=roles["bank"], is_active=True)


@pytest.mark.django_db
def test_access_context_returns_roles_permissions_and_scopes(hierarchy, roles):
    user = User.objects.create_user(username="ctx_user", password="pass")
    _make_access(user, roles["bank"], organization=hierarchy["org"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/access/user-resource-accesses/access_context/")
    assert response.status_code == 200
    payload = response.json()

    assert payload["user"]["username"] == "ctx_user"
    assert {item["code"] for item in payload["roles"]} == {"BANK_ADMIN"}
    assert {"device.view", "organization.view", "access.view", "access.manage"}.issubset({item["code"] for item in payload["permissions"]})
    assert [item["code"] for item in payload["organizations"]] == ["DHAKA_BANK"]
    assert {item["code"] for item in payload["data_centers"]} == {"PDC", "BDC"}
    assert {item["code"] for item in payload["rooms"]} == {"R1", "R2", "R3"}
    assert {item["code"] for item in payload["racks"]} == {"RK1", "RK2", "RK3"}
    assert {item["code"] for item in payload["devices"]} == {"UPS-01", "PAC-01", "UPS-02", "ATS-01"}


@pytest.mark.django_db
def test_legacy_my_access_alias_still_works(hierarchy, roles):
    user = User.objects.create_user(username="legacy_user", password="pass")
    _make_access(user, roles["bank"], organization=hierarchy["org"])

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1/access/user-data-center-roles/my_access/")
    assert response.status_code == 200
    assert len(response.json()) == 1
