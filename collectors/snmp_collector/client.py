import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Union

from celery.exceptions import SoftTimeLimitExceeded

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

SNMP_BATCH_SIZE = 10

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
    """Synchronous SNMP client with one engine and bounded OID batches per poll."""

    def __init__(self, protocol_config: DeviceProtocolConfig, credential: DeviceCredential):
        self.protocol_config = protocol_config
        self.credential = credential
        self.host = protocol_config.host
        self.port = protocol_config.port or 161
        self.timeout = int(protocol_config.timeout_seconds or 5)
        self.retries = int(protocol_config.retry_count or 1)
        self.engine = SnmpEngine()
        self.auth_data = self._auth_data()
        self.transport = UdpTransportTarget(
            (self.host, self.port),
            timeout=self.timeout,
            retries=self.retries,
        )
        self.context = ContextData()

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
        results = self._request([oid])
        result = results[0]
        if isinstance(result, Exception):
            raise result
        return result

    def _request(self, oids: List[str]) -> List[Union[SNMPResult, Exception]]:
        iterator = getCmd(
            self.engine,
            self.auth_data,
            self.transport,
            self.context,
            *[ObjectType(ObjectIdentity(oid)) for oid in oids],
        )
        try:
            error_indication, error_status, error_index, var_binds = next(iterator)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            # PySNMP may wrap the Celery signal while it is importing a MIB.
            if "SoftTimeLimitExceeded" in repr(exc):
                raise SNMPTimeoutError("SNMP request interrupted by the worker time limit") from exc
            raise

        if error_indication:
            msg = str(error_indication)
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                error = SNMPTimeoutError("SNMP device request timed out")
            else:
                error = SNMPResponseError(msg)
            return [error for _ in oids]
        if error_status:
            failing_index = int(error_index) - 1 if error_index else 0
            failing_oid = oids[failing_index] if 0 <= failing_index < len(oids) else oids[0]
            error = SNMPResponseError(f"{error_status.prettyPrint()} at {failing_oid}")
            return [error for _ in oids]
        if len(var_binds) != len(oids):
            error = SNMPResponseError("SNMP response did not contain all requested OIDs")
            return [error for _ in oids]

        return [
            SNMPResult(oid=str(name), value=value, raw_value=value.prettyPrint())
            for name, value in var_binds
        ]

    def get_many(self, oids: Iterable[str], batch_size: int = SNMP_BATCH_SIZE) -> Dict[str, Union[SNMPResult, Exception]]:
        requested = [str(oid).strip() for oid in oids if str(oid).strip()]
        results: Dict[str, Union[SNMPResult, Exception]] = {}
        batch_size = max(1, int(batch_size or SNMP_BATCH_SIZE))
        for start in range(0, len(requested), batch_size):
            batch = requested[start:start + batch_size]
            batch_results = self._request(batch)
            results.update(dict(zip(batch, batch_results)))
        return results
