from celery import shared_task
from .services import process_snmp_trap


@shared_task(queue="traps", autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def process_snmp_trap_task(source_ip, trap_oid, raw_varbinds):
    event = process_snmp_trap(source_ip=source_ip, trap_oid=trap_oid, raw_varbinds=raw_varbinds)
    return {"trap_event_id": str(event.id), "source_ip": source_ip, "trap_oid": trap_oid, "is_mapped": event.is_mapped}


@shared_task(queue="traps", autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def process_pac_trap_alarm_confirmation_task(device_id, trap_event_id, event_code):
    from .pac_alarm_handler import process_pac_trap_alarm_confirmation

    return process_pac_trap_alarm_confirmation(device_id=device_id, trap_event_id=trap_event_id, event_code=event_code)
