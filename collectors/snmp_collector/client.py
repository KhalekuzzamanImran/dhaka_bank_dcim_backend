import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    getCmd,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
)

from apps.devices.models import DeviceCredential, DeviceProtocolConfig, SNMPVersion
from .exceptions import SNMPCredentialError, SNMPResponseError, SNMPTimeoutError
from .security import decrypt_secret

logger = logging.getLogger(__name__)

_AUTH_PROTOCOLS = {
    None: usmNoAuthProtocol,
    "": usmNoAuthProtocol,
    "NONE": usmNoAuthProtocol,
    "MD5": usmHMACMD5AuthProtocol,
    "SHA": usmHMACSHAAuthProtocol,
    "SHA1": usmHMACSHAAuthProtocol,
    "SHA224": usmHMAC128SHA224AuthProtocol,
    "SHA256": usmHMAC192SHA256AuthProtocol,
    "SHA384": usmHMAC256SHA384AuthProtocol,
    "SHA512": usmHMAC384SHA512AuthProtocol,
}

_PRIV_PROTOCOLS = {
    None: usmNoPrivProtocol,
    "": usmNoPrivProtocol,
    "NONE": usmNoPrivProtocol,
    "DES": usmDESPrivProtocol,
    "AES": usmAesCfb128Protocol,
    "AES128": usmAesCfb128Protocol,
    "AES192": usmAesCfb192Protocol,
    "AES256": usmAesCfb256Protocol,
}


@dataclass(frozen=True)
class SNMPResult:
    oid: str
    value: Any
    raw_value: str


class SNMPClient:
    """Small synchronous SNMP client for Celery worker usage.

    The worker calls one OID at a time to keep error reporting precise. For very large fleets,
    shard workers by data center and reduce OID count through vendor-specific batching later.
    """

    def __init__(self, protocol_config: DeviceProtocolConfig, credential: DeviceCredential):
        self.protocol_config = protocol_config
        self.credential = credential
        self.host = protocol_config.host
        self.port = protocol_config.port or 161
        self.timeout = int(protocol_config.timeout_seconds or 5)
        self.retries = int(protocol_config.retry_count or 1)

    def _auth_data(self):
        version = self.credential.snmp_version or SNMPVersion.V2C
        if version == SNMPVersion.V1:
            community = decrypt_secret(self.credential.snmp_community_encrypted)
            if not community:
                raise SNMPCredentialError("SNMP v1 community is missing")
            return CommunityData(community, mpModel=0)
        if version == SNMPVersion.V2C:
            community = decrypt_secret(self.credential.snmp_community_encrypted)
            if not community:
                raise SNMPCredentialError("SNMP v2c community is missing")
            return CommunityData(community, mpModel=1)
        if version == SNMPVersion.V3:
            username = self.credential.username
            if not username:
                raise SNMPCredentialError("SNMP v3 username is missing")
            auth_key = decrypt_secret(self.credential.snmp_v3_auth_key_encrypted)
            priv_key = decrypt_secret(self.credential.snmp_v3_priv_key_encrypted)
            auth_proto = _AUTH_PROTOCOLS.get((self.credential.snmp_v3_auth_protocol or "NONE").upper())
            priv_proto = _PRIV_PROTOCOLS.get((self.credential.snmp_v3_priv_protocol or "NONE").upper())
            if auth_proto is None:
                raise SNMPCredentialError(f"Unsupported SNMP v3 auth protocol: {self.credential.snmp_v3_auth_protocol}")
            if priv_proto is None:
                raise SNMPCredentialError(f"Unsupported SNMP v3 privacy protocol: {self.credential.snmp_v3_priv_protocol}")
            return UsmUserData(
                userName=username,
                authKey=auth_key,
                privKey=priv_key,
                authProtocol=auth_proto,
                privProtocol=priv_proto,
            )
        raise SNMPCredentialError(f"Unsupported SNMP version: {version}")

    def get(self, oid: str) -> SNMPResult:
        iterator = getCmd(
            SnmpEngine(),
            self._auth_data(),
            UdpTransportTarget((self.host, self.port), timeout=self.timeout, retries=self.retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        error_indication, error_status, error_index, var_binds = next(iterator)
        if error_indication:
            msg = str(error_indication)
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                raise SNMPTimeoutError(msg)
            raise SNMPResponseError(msg)
        if error_status:
            failing = var_binds[int(error_index) - 1][0] if error_index else oid
            raise SNMPResponseError(f"{error_status.prettyPrint()} at {failing}")
        if not var_binds:
            raise SNMPResponseError(f"No SNMP response for OID {oid}")
        name, value = var_binds[0]
        return SNMPResult(oid=str(name), value=value, raw_value=value.prettyPrint())

    def get_many(self, oids: Iterable[str]) -> Dict[str, SNMPResult]:
        results: Dict[str, SNMPResult] = {}
        for oid in oids:
            results[oid] = self.get(oid)
        return results
