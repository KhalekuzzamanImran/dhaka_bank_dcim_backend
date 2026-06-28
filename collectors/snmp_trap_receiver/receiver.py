"""Long-running SNMP trap receiver.

Listens on UDP 1162 inside the container. Map host UDP 162 to container UDP 1162.
Supports common SNMPv1/v2 trap parsing via pysnmp low-level API and pushes parsed traps to Celery.
"""
import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.getenv("DJANGO_SETTINGS_MODULE", "config.settings.production"))
django.setup()

from pyasn1.codec.ber import decoder
from pysnmp.carrier.asyncore.dispatch import AsyncoreDispatcher
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.proto import api

from collectors.snmp_trap_receiver.tasks import process_snmp_trap_task

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

SNMP_TRAP_OID_V2 = "1.3.6.1.6.3.1.1.4.1.0"


def cb_fun(transport_dispatcher, transport_domain, transport_address, whole_msg):
    while whole_msg:
        msg_ver = int(api.decodeMessageVersion(whole_msg))
        if msg_ver not in api.protoModules:
            logger.warning("Unsupported SNMP message version: %s", msg_ver)
            return whole_msg
        p_mod = api.protoModules[msg_ver]
        req_msg, whole_msg = decoder.decode(whole_msg, asn1Spec=p_mod.Message())
        pdu = p_mod.apiMessage.getPDU(req_msg)
        source_ip = transport_address[0]
        raw_varbinds = {}
        trap_oid = None
        try:
            for oid, value in p_mod.apiPDU.getVarBinds(pdu):
                oid_str = oid.prettyPrint()
                value_str = value.prettyPrint()
                raw_varbinds[oid_str] = value_str
                if oid_str == SNMP_TRAP_OID_V2:
                    trap_oid = value_str
        except Exception:
            logger.exception("Failed parsing varbinds from trap source=%s", source_ip)
        trap_oid = trap_oid or raw_varbinds.get(SNMP_TRAP_OID_V2) or "UNKNOWN"
        process_snmp_trap_task.delay(source_ip=source_ip, trap_oid=trap_oid, raw_varbinds=raw_varbinds)
        logger.info("Queued SNMP trap source=%s trap_oid=%s varbinds=%s", source_ip, trap_oid, len(raw_varbinds))
    return whole_msg


def run():
    host = os.getenv("SNMP_TRAP_LISTEN_HOST", "0.0.0.0")
    port = int(os.getenv("SNMP_TRAP_LISTEN_PORT", "1162"))
    dispatcher = AsyncoreDispatcher()
    dispatcher.registerRecvCbFun(cb_fun)
    dispatcher.registerTransport(udp.domainName, udp.UdpSocketTransport().openServerMode((host, port)))
    logger.info("SNMP Trap Receiver listening on %s:%s/udp", host, port)
    dispatcher.jobStarted(1)
    try:
        dispatcher.runDispatcher()
    finally:
        dispatcher.closeDispatcher()


if __name__ == "__main__":
    run()
