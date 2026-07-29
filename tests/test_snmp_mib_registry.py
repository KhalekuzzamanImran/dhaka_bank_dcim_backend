from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertStatus
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import DeviceEvent
from apps.traps.models import SNMPMIBDefinition, SNMPTrapEvent, SNMPTrapOIDMapping, SNMPTrapSource, TrapResolutionSource
from apps.traps.services import MIBRegistry, import_snmp_mib_file, invalidate_mib_registry_cache
from collectors.snmp_trap_receiver.services import process_snmp_trap


MIB_FILE_TEMPLATE = """
TestTrap-MIB DEFINITIONS ::= BEGIN

enterprises OBJECT IDENTIFIER ::= { 1 3 6 1 4 1 }
apc OBJECT IDENTIFIER ::= { enterprises 318 }

apcTestTrap TRAP-TYPE
   ENTERPRISE apc
   VARIABLES { mtrapargsString }
   DESCRIPTION
      "Trap used to test SNMP trap functionality."
   ::= 636

END
"""


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
        name="UPS-01",
        code="UPS-01",
        ip_address="10.10.10.50",
    )
    SNMPTrapSource.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        source_ip="10.10.10.50",
        is_enabled=True,
    )
    return org, dc, device


def _write_mib_file(tmp_path: Path, file_name: str = "TestTrap-MIB.mib") -> Path:
    mib_file = tmp_path / file_name
    mib_file.write_text(MIB_FILE_TEMPLATE.strip() + "\n", encoding="utf-8")
    return mib_file


@pytest.mark.django_db
@override_settings(
    SNMP_MIB_FALLBACK_ENABLED=True,
    SNMP_UNMAPPED_TRAP_CREATE_REVIEW_ALERT=True,
    SNMP_MIB_CACHE_TTL_SECONDS=60,
)
def test_import_snmp_mib_file_registers_and_resolves_definition(tmp_path):
    mib_file = _write_mib_file(tmp_path)

    summary = import_snmp_mib_file(mib_file)
    definition = MIBRegistry().resolve("1.3.6.1.4.1.318.0.636")

    assert summary["created"] == 1
    assert summary["updated"] == 0
    assert summary["deactivated"] == 0
    assert definition is not None
    assert definition.module == "TestTrap-MIB"
    assert definition.symbol == "apcTestTrap"
    assert definition.oid == "1.3.6.1.4.1.318.0.636"
    assert definition.description == "Trap used to test SNMP trap functionality."


@pytest.mark.django_db
@override_settings(
    SNMP_MIB_FALLBACK_ENABLED=True,
    SNMP_UNMAPPED_TRAP_CREATE_REVIEW_ALERT=True,
    SNMP_MIB_CACHE_TTL_SECONDS=60,
)
def test_mib_registry_caches_positive_and_negative_lookups(tmp_path):
    mib_file = _write_mib_file(tmp_path)
    import_snmp_mib_file(mib_file)
    invalidate_mib_registry_cache()
    cache.clear()

    registry = MIBRegistry()
    with patch.object(SNMPMIBDefinition.objects, "filter", wraps=SNMPMIBDefinition.objects.filter) as filter_mock:
        assert registry.resolve("1.3.6.1.4.1.318.0.636") is not None
        assert filter_mock.call_count == 1
        assert registry.resolve("1.3.6.1.4.1.318.0.636") is not None
        assert filter_mock.call_count == 1

    invalidate_mib_registry_cache()
    cache.clear()
    with patch.object(SNMPMIBDefinition.objects, "filter", wraps=SNMPMIBDefinition.objects.filter) as filter_mock:
        assert registry.resolve("1.3.6.1.4.1.318.0.999") is None
        assert filter_mock.call_count == 1
        assert registry.resolve("1.3.6.1.4.1.318.0.999") is None
        assert filter_mock.call_count == 1


@pytest.mark.django_db
@override_settings(
    SNMP_MIB_FALLBACK_ENABLED=True,
    SNMP_UNMAPPED_TRAP_CREATE_REVIEW_ALERT=True,
    SNMP_MIB_CACHE_TTL_SECONDS=60,
)
def test_process_snmp_trap_uses_mib_fallback_when_mapping_missing(tmp_path):
    _write_mib_file(tmp_path)
    with override_settings(SNMP_MIB_DIRECTORIES=[str(tmp_path)]):
        import_snmp_mib_file(tmp_path / "TestTrap-MIB.mib")
        _, _, device = _build_device()

        event = process_snmp_trap(
            source_ip=device.ip_address,
            trap_oid="1.3.6.1.4.1.318.0.636",
            raw_varbinds={"1.3.6.1.2.1.1.3.0": "123"},
        )

    event.refresh_from_db()
    assert event.resolution_source == TrapResolutionSource.MIB
    assert event.is_mapped is False
    assert event.requires_mapping_review is True
    assert event.mib_module == "TestTrap-MIB"
    assert event.mib_symbol == "apcTestTrap"
    assert event.mib_description == "Trap used to test SNMP trap functionality."
    assert event.raw_varbinds["_resolution_source"] == TrapResolutionSource.MIB
    assert event.raw_varbinds["_mib_symbol"] == "apcTestTrap"
    assert DeviceEvent.objects.filter(device=device, event_code="UNMAPPED_SNMP_TRAP").count() == 1
    alert = AlertEvent.objects.get(device=device, metadata__trap_oid="1.3.6.1.4.1.318.0.636")
    assert alert.status == AlertStatus.OPEN
    assert alert.occurrence_count == 1
    assert alert.message.startswith("Unmapped SNMP trap received.")


@pytest.mark.django_db
@override_settings(
    SNMP_MIB_FALLBACK_ENABLED=True,
    SNMP_UNMAPPED_TRAP_CREATE_REVIEW_ALERT=True,
    SNMP_MIB_CACHE_TTL_SECONDS=60,
)
def test_database_mapping_takes_priority_over_mib_fallback(tmp_path):
    _write_mib_file(tmp_path)
    import_snmp_mib_file(tmp_path / "TestTrap-MIB.mib")
    _, _, device = _build_device()
    SNMPTrapOIDMapping.objects.create(
        device_type=device.device_type,
        vendor=device.device_model.vendor,
        device_model=device.device_model,
        trap_oid="1.3.6.1.4.1.318.0.636",
        event_code="APC_TEST_TRAP",
        event_name="APC SNMP Test Trap",
        severity="INFO",
        message_template="APC SNMP test trap",
        create_alert=False,
        is_active=True,
    )

    event = process_snmp_trap(
        source_ip=device.ip_address,
        trap_oid="1.3.6.1.4.1.318.0.636",
        raw_varbinds={"1.3.6.1.2.1.1.3.0": "123"},
    )

    event.refresh_from_db()
    assert event.resolution_source == TrapResolutionSource.DATABASE
    assert event.is_mapped is True
    assert event.event_code == "APC_TEST_TRAP"
    assert event.event_name == "APC SNMP Test Trap"
    assert event.mib_module is None
    assert event.requires_mapping_review is False
    assert DeviceEvent.objects.filter(device=device, event_code="APC_TEST_TRAP").count() == 1
    assert AlertEvent.objects.filter(device=device, metadata__trap_oid="1.3.6.1.4.1.318.0.636", status=AlertStatus.OPEN).count() == 0


@pytest.mark.django_db
@override_settings(
    SNMP_MIB_FALLBACK_ENABLED=True,
    SNMP_UNMAPPED_TRAP_CREATE_REVIEW_ALERT=True,
    SNMP_MIB_CACHE_TTL_SECONDS=60,
)
def test_unknown_trap_remains_unknown_and_updates_review_alert(tmp_path):
    _write_mib_file(tmp_path)
    import_snmp_mib_file(tmp_path / "TestTrap-MIB.mib")
    _, _, device = _build_device()

    first = process_snmp_trap(
        source_ip=device.ip_address,
        trap_oid="1.3.6.1.4.1.318.0.999",
        raw_varbinds={},
    )
    second = process_snmp_trap(
        source_ip=device.ip_address,
        trap_oid="1.3.6.1.4.1.318.0.999",
        raw_varbinds={},
    )

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.resolution_source == TrapResolutionSource.UNKNOWN
    assert first.is_mapped is False
    assert first.mib_symbol is None
    assert first.requires_mapping_review is True
    assert AlertEvent.objects.filter(device=device, metadata__trap_oid="1.3.6.1.4.1.318.0.999", status=AlertStatus.OPEN).count() == 1
    alert = AlertEvent.objects.get(device=device, metadata__trap_oid="1.3.6.1.4.1.318.0.999")
    assert alert.occurrence_count == 2
    assert "No matching MIB definition" in alert.message
