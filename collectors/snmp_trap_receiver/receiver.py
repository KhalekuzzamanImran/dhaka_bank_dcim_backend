"""Long-running SNMP trap receiver.

Listens on UDP 1162 inside the container. Map host UDP 162 to container UDP 1162.
Supports common SNMPv1/v2 trap parsing via pysnmp low-level API and pushes parsed traps to Celery.
"""
import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.getenv("DJANGO_SETTINGS_MODULE", "config.settings.production"))
django.setup()

from .parsing import decode_snmp_trap_message
from collectors.snmp_trap_receiver.tasks import process_snmp_trap_task

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

def cb_fun(_transport_dispatcher, _transport_domain, transport_address, whole_msg):
    while whole_msg:
        try:
            trap_oid, raw_varbinds, whole_msg = decode_snmp_trap_message(whole_msg, transport_address)
        except Exception:
            logger.exception("Failed processing SNMP trap source=%s", transport_address[0] if transport_address else "unknown")
            return b""

        source_ip = transport_address[0]
        try:
            process_snmp_trap_task.delay(source_ip=source_ip, trap_oid=trap_oid, raw_varbinds=raw_varbinds)
        except Exception:
            logger.exception("Failed to queue SNMP trap source=%s trap_oid=%s", source_ip, trap_oid)
            return b""
        logger.info("Queued SNMP trap source=%s trap_oid=%s varbinds=%s", source_ip, trap_oid, len(raw_varbinds))
    return whole_msg


def run():
    from pysnmp.carrier.asyncore.dispatch import AsyncoreDispatcher
    from pysnmp.carrier.asyncore.dgram import udp

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
