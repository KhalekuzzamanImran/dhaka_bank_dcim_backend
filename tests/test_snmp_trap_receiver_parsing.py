from types import SimpleNamespace

import pytest

from collectors.snmp_trap_receiver import receiver
from collectors.snmp_trap_receiver.parsing import (
    SNMP_TRAP_OID_V2,
    derive_snmpv1_trap_oid,
    extract_snmpv1_trap_oid,
    extract_snmpv2c_trap_oid,
    extract_varbinds,
    normalize_oid,
)


class _FakeValue:
    def __init__(self, value):
        self.value = value

    def prettyPrint(self):
        return str(self.value)


class _FakePdu:
    def __init__(self, *, enterprise, agent_addr, generic_trap, specific_trap, timestamp, varbinds):
        self.enterprise = _FakeValue(enterprise)
        self.agent_addr = _FakeValue(agent_addr)
        self.generic_trap = _FakeValue(generic_trap)
        self.specific_trap = _FakeValue(specific_trap)
        self.timestamp = _FakeValue(timestamp)
        self.varbinds = [
            (_FakeValue(oid), _FakeValue(value))
            for oid, value in varbinds
        ]


class _FakeTrapApi:
    def getEnterprise(self, pdu):
        return pdu.enterprise

    def getAgentAddr(self, pdu):
        return pdu.agent_addr

    def getGenericTrap(self, pdu):
        return pdu.generic_trap

    def getSpecificTrap(self, pdu):
        return pdu.specific_trap

    def getTimeStamp(self, pdu):
        return pdu.timestamp

    def getVarBinds(self, pdu):
        return pdu.varbinds


class _FakePduApi:
    def getVarBinds(self, pdu):
        return pdu.varbinds


def _fake_p_mod():
    return SimpleNamespace(apiTrapPDU=_FakeTrapApi(), apiPDU=_FakePduApi())


def test_normalize_oid_strips_leading_dot():
    assert normalize_oid(".1.3.6.1.4.1.318") == "1.3.6.1.4.1.318"


@pytest.mark.parametrize(
    "generic_trap, expected",
    [
        (0, "1.3.6.1.6.3.1.1.5.1"),
        (1, "1.3.6.1.6.3.1.1.5.2"),
        (2, "1.3.6.1.6.3.1.1.5.3"),
        (3, "1.3.6.1.6.3.1.1.5.4"),
        (4, "1.3.6.1.6.3.1.1.5.5"),
        (5, "1.3.6.1.6.3.1.1.5.6"),
    ],
)
def test_derive_snmpv1_trap_oid_for_standard_generic_traps(generic_trap, expected):
    assert derive_snmpv1_trap_oid("1.3.6.1.4.1.318", generic_trap, 77) == expected


def test_derive_snmpv1_trap_oid_for_enterprise_specific_trap():
    assert derive_snmpv1_trap_oid("1.3.6.1.4.1.318", 6, 77) == "1.3.6.1.4.1.318.0.77"


def test_derive_snmpv1_trap_oid_accepts_leading_dot_enterprise_oid():
    assert derive_snmpv1_trap_oid(".1.3.6.1.4.1.318", 6, 77) == "1.3.6.1.4.1.318.0.77"


@pytest.mark.parametrize(
    "enterprise_oid, generic_trap, specific_trap, message",
    [
        (None, 6, 77, "OID value cannot be empty."),
        ("1.3.6.1.4.1.318", 9, 77, "Unsupported SNMPv1 generic trap number: 9."),
        ("1.3.6.1.4.1.318", 6, -1, "specific trap must be a non-negative integer."),
        ("1.3.6.1.4.1.318", "enterprise", 77, "generic trap must be an integer."),
        ("1.3.6.1.4.1.318", 6, "specific", "specific trap must be an integer."),
    ],
)
def test_derive_snmpv1_trap_oid_validation(enterprise_oid, generic_trap, specific_trap, message):
    with pytest.raises(ValueError, match=message):
        derive_snmpv1_trap_oid(enterprise_oid, generic_trap, specific_trap)


def test_extract_varbinds_returns_json_safe_strings():
    p_mod = _fake_p_mod()
    pdu = _FakePdu(
        enterprise="1.3.6.1.4.1.318",
        agent_addr="10.10.10.1",
        generic_trap=6,
        specific_trap=77,
        timestamp=100,
        varbinds=[("1.3.6.1.2.1.1.3.0", 123), (SNMP_TRAP_OID_V2, ".1.3.6.1.4.1.318.0.77")],
    )

    varbinds = extract_varbinds(p_mod, pdu, msg_ver=0)
    assert varbinds["1.3.6.1.2.1.1.3.0"] == "123"
    assert varbinds[SNMP_TRAP_OID_V2] == ".1.3.6.1.4.1.318.0.77"


def test_extract_snmpv1_trap_oid_includes_metadata():
    p_mod = _fake_p_mod()
    pdu = _FakePdu(
        enterprise=".1.3.6.1.4.1.318",
        agent_addr="172.25.210.123",
        generic_trap=6,
        specific_trap=77,
        timestamp=42,
        varbinds=[],
    )

    trap_oid, metadata = extract_snmpv1_trap_oid(
        p_mod,
        pdu,
        transport_source_ip="172.25.210.146",
        transport_source_port=162,
    )

    assert trap_oid == "1.3.6.1.4.1.318.0.77"
    assert metadata["_snmp_version"] == "SNMPv1"
    assert metadata["_snmp_v1_enterprise"] == "1.3.6.1.4.1.318"
    assert metadata["_snmp_v1_agent_address"] == "172.25.210.123"
    assert metadata["_snmp_v1_generic_trap"] == 6
    assert metadata["_snmp_v1_specific_trap"] == 77
    assert metadata["_snmp_v1_timestamp"] == 42
    assert metadata["_canonical_trap_oid"] == "1.3.6.1.4.1.318.0.77"
    assert metadata["_transport_source_ip"] == "172.25.210.146"
    assert metadata["_transport_source_port"] == 162


def test_extract_snmpv2c_trap_oid():
    trap_oid, metadata = extract_snmpv2c_trap_oid({SNMP_TRAP_OID_V2: ".1.3.6.1.4.1.318.0.77"})

    assert trap_oid == "1.3.6.1.4.1.318.0.77"
    assert metadata["_canonical_trap_oid"] == "1.3.6.1.4.1.318.0.77"
    assert metadata["_snmp_version"] == "SNMPv2c"


def test_extract_snmpv2c_trap_oid_missing_mandatory_varbind():
    with pytest.raises(ValueError, match="missing mandatory snmpTrapOID.0 varbind"):
        extract_snmpv2c_trap_oid({})


def test_cb_fun_queues_canonical_v1_trap_oid(monkeypatch):
    monkeypatch.setattr(
        receiver,
        "decode_snmp_trap_message",
        lambda whole_msg, transport_address: (
            "1.3.6.1.4.1.318.0.77",
            {
                "_canonical_trap_oid": "1.3.6.1.4.1.318.0.77",
                "_snmp_version": "SNMPv1",
                "_transport_source_ip": "172.25.210.146",
                "_transport_source_port": 162,
            },
            b"",
        ),
    )
    delay_calls = []
    monkeypatch.setattr(
        receiver.process_snmp_trap_task,
        "delay",
        lambda **kwargs: delay_calls.append(kwargs),
    )

    remainder = receiver.cb_fun(None, None, ("172.25.210.146", 162), b"packet")

    assert remainder == b""
    assert delay_calls == [
        {
            "source_ip": "172.25.210.146",
            "trap_oid": "1.3.6.1.4.1.318.0.77",
            "raw_varbinds": {
                "_canonical_trap_oid": "1.3.6.1.4.1.318.0.77",
                "_snmp_version": "SNMPv1",
                "_transport_source_ip": "172.25.210.146",
                "_transport_source_port": 162,
            },
        }
    ]


def test_cb_fun_logs_malformed_packet_and_does_not_queue(monkeypatch, caplog):
    monkeypatch.setattr(
        receiver,
        "decode_snmp_trap_message",
        lambda whole_msg, transport_address: (_ for _ in ()).throw(ValueError("bad BER packet")),
    )
    delay_calls = []
    monkeypatch.setattr(
        receiver.process_snmp_trap_task,
        "delay",
        lambda **kwargs: delay_calls.append(kwargs),
    )

    with caplog.at_level("ERROR"):
        remainder = receiver.cb_fun(None, None, ("172.25.210.146", 162), b"packet")

    assert remainder == b""
    assert delay_calls == []
    assert "bad BER packet" in caplog.text
