import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

SNMP_TRAP_OID_V2 = "1.3.6.1.6.3.1.1.4.1.0"
SNMP_PYSNMP_VERSION_V1 = 0
SNMP_PYSNMP_VERSION_V2C = 1

SNMPV1_GENERIC_TRAP_OIDS = {
    0: "1.3.6.1.6.3.1.1.5.1",
    1: "1.3.6.1.6.3.1.1.5.2",
    2: "1.3.6.1.6.3.1.1.5.3",
    3: "1.3.6.1.6.3.1.1.5.4",
    4: "1.3.6.1.6.3.1.1.5.5",
    5: "1.3.6.1.6.3.1.1.5.6",
}


def _patch_pysnmp_unsigned_types():
    """Relax pyasn1 unsigned 32/64-bit integer constraints to handle buggy firmware.

    Many network devices (such as Rack PDUs, UPSs, and switches) encode 32-bit
    unsigned integers (TimeTicks, Counter32, Gauge32) with the high bit set without
    a leading 0x00 pad byte. BER decodes these as negative integers (e.g. -2030124646),
    which violates pyasn1's ValueRangeConstraint(0, 4294967295) and crashes decoding.
    Masking with 0xFFFFFFFF recovers the correct 32-bit unsigned value (e.g. 2264842650).
    """
    try:
        import pysnmp.proto.rfc1155 as rfc1155
        import pysnmp.proto.rfc1902 as rfc1902

        def _patch_type(cls, bitmask: int):
            if getattr(cls, "_dcim_unsigned_patched", False):
                return
            orig_init = cls.__init__

            def _safe_init(self, *args, **kwargs):
                if args:
                    val = args[0]
                    if isinstance(val, int) and val < 0:
                        args = (val & bitmask, *args[1:])
                elif "value" in kwargs:
                    val = kwargs["value"]
                    if isinstance(val, int) and val < 0:
                        kwargs["value"] = val & bitmask
                orig_init(self, *args, **kwargs)

            cls.__init__ = _safe_init
            cls._dcim_unsigned_patched = True

        for u32_cls in (
            rfc1155.TimeTicks,
            rfc1155.Counter,
            rfc1155.Gauge,
            rfc1902.TimeTicks,
            rfc1902.Counter32,
            rfc1902.Gauge32,
            rfc1902.Unsigned32,
        ):
            _patch_type(u32_cls, 0xFFFFFFFF)

        _patch_type(rfc1902.Counter64, 0xFFFFFFFFFFFFFFFF)
    except Exception as exc:
        logger.warning("Could not apply pyasn1 unsigned integer patch: %s", exc)


_patch_pysnmp_unsigned_types()


def _load_pysnmp_modules():
    _patch_pysnmp_unsigned_types()
    from pyasn1.codec.ber import decoder
    from pysnmp.proto import api

    return decoder, api


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "prettyPrint"):
        return str(value.prettyPrint())
    return str(value)


def normalize_oid(value: Any) -> str:
    """Normalize an OID-like value to a dotted string without a leading dot."""

    normalized = _text_value(value).strip().lstrip(".")
    if not normalized:
        raise ValueError("OID value cannot be empty.")
    return normalized


def _coerce_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field_name} must be an integer.")
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(_text_value(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer.") from exc


def _coerce_non_negative_int(value: Any, *, field_name: str) -> int:
    number = _coerce_int(value, field_name=field_name)
    if number < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")
    return number


def derive_snmpv1_trap_oid(enterprise_oid: Any, generic_trap: Any, specific_trap: Any) -> str:
    """Convert an SNMPv1 TrapPDU into its canonical notification OID."""

    generic = _coerce_int(generic_trap, field_name="generic trap")
    if generic in SNMPV1_GENERIC_TRAP_OIDS:
        return SNMPV1_GENERIC_TRAP_OIDS[generic]
    if generic != 6:
        raise ValueError(f"Unsupported SNMPv1 generic trap number: {generic}.")

    enterprise = normalize_oid(enterprise_oid)
    specific = _coerce_non_negative_int(specific_trap, field_name="specific trap")
    return f"{enterprise}.0.{specific}"


def extract_varbinds(p_mod, pdu, *, msg_ver: int) -> dict[str, str]:
    """Return raw varbinds as JSON-safe strings."""

    try:
        if msg_ver == SNMP_PYSNMP_VERSION_V1:
            var_binds = p_mod.apiTrapPDU.getVarBinds(pdu)
        else:
            var_binds = p_mod.apiPDU.getVarBinds(pdu)
    except Exception as exc:
        raise ValueError(f"Failed to extract trap varbinds: {exc}") from exc

    raw_varbinds: dict[str, str] = {}
    for oid, value in var_binds:
        raw_varbinds[normalize_oid(oid)] = _text_value(value)
    return raw_varbinds


def extract_snmpv2c_trap_oid(raw_varbinds: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract the canonical trap OID from an SNMPv2c notification payload."""

    trap_oid = raw_varbinds.get(SNMP_TRAP_OID_V2) or raw_varbinds.get(normalize_oid(SNMP_TRAP_OID_V2))
    if trap_oid in (None, ""):
        raise ValueError("SNMPv2c trap is missing mandatory snmpTrapOID.0 varbind.")

    canonical_oid = normalize_oid(trap_oid)
    metadata = {
        "_canonical_trap_oid": canonical_oid,
        "_snmp_version": "SNMPv2c",
    }
    return canonical_oid, metadata


def extract_snmpv1_trap_oid(p_mod, pdu, *, transport_source_ip: str, transport_source_port: int | None) -> tuple[str, dict[str, Any]]:
    """Extract a canonical trap OID and metadata from an SNMPv1 TrapPDU."""

    try:
        enterprise_oid = p_mod.apiTrapPDU.getEnterprise(pdu)
        agent_address = p_mod.apiTrapPDU.getAgentAddr(pdu)
        generic_trap = p_mod.apiTrapPDU.getGenericTrap(pdu)
        specific_trap = p_mod.apiTrapPDU.getSpecificTrap(pdu)
        timestamp = p_mod.apiTrapPDU.getTimeStamp(pdu)
    except Exception as exc:
        raise ValueError(f"Failed to read SNMPv1 trap header fields: {exc}") from exc

    canonical_oid = derive_snmpv1_trap_oid(enterprise_oid, generic_trap, specific_trap)
    metadata = {
        "_snmp_version": "SNMPv1",
        "_snmp_v1_enterprise": normalize_oid(enterprise_oid),
        "_snmp_v1_agent_address": _text_value(agent_address).strip(),
        "_snmp_v1_generic_trap": _coerce_int(generic_trap, field_name="generic trap"),
        "_snmp_v1_specific_trap": _coerce_non_negative_int(specific_trap, field_name="specific trap"),
        "_snmp_v1_timestamp": _coerce_int(timestamp, field_name="timestamp"),
        "_canonical_trap_oid": canonical_oid,
        "_transport_source_ip": transport_source_ip,
        "_transport_source_port": transport_source_port,
    }
    return canonical_oid, metadata


def decode_snmp_trap_message(whole_msg: bytes, transport_address) -> tuple[str, dict[str, Any], bytes]:
    """Decode one SNMP message and return its canonical trap OID plus raw payload."""

    decoder, api = _load_pysnmp_modules()
    msg_ver = int(api.decodeMessageVersion(whole_msg))
    if msg_ver not in api.protoModules:
        raise ValueError(f"Unsupported SNMP message version: {msg_ver}.")

    p_mod = api.protoModules[msg_ver]
    try:
        req_msg, remainder = decoder.decode(whole_msg, asn1Spec=p_mod.Message())
    except Exception as exc:
        raise ValueError(f"Failed to decode SNMP message version {msg_ver}: {exc}") from exc

    pdu = p_mod.apiMessage.getPDU(req_msg)
    if pdu is None:
        raise ValueError("SNMP message did not contain a PDU.")

    raw_varbinds = extract_varbinds(p_mod, pdu, msg_ver=msg_ver)
    transport_source_ip = transport_address[0] if transport_address else ""
    transport_source_port = transport_address[1] if transport_address and len(transport_address) > 1 else None

    # Keep the UDP transport source for device lookup. SNMPv1 agentAddress may
    # be rewritten by a trusted relay, so it is preserved as metadata only.
    if msg_ver == SNMP_PYSNMP_VERSION_V1:
        canonical_oid, metadata = extract_snmpv1_trap_oid(
            p_mod,
            pdu,
            transport_source_ip=transport_source_ip,
            transport_source_port=transport_source_port,
        )
    elif msg_ver == SNMP_PYSNMP_VERSION_V2C:
        canonical_oid, metadata = extract_snmpv2c_trap_oid(raw_varbinds)
        metadata.update(
            {
                "_transport_source_ip": transport_source_ip,
                "_transport_source_port": transport_source_port,
            }
        )
    else:
        raise ValueError(f"Unsupported SNMP message version: {msg_ver}.")

    raw_varbinds.update(metadata)
    return canonical_oid, raw_varbinds, remainder
