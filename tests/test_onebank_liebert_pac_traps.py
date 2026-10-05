from apps.traps.management.commands.seed_onebank_liebert_pac_traps import PAC_TRAPS
from collectors.snmp_trap_receiver.pac_alarm_handler import is_pac_confirmation_trap


def test_liebert_condition_notifications_match_pac_confirmation_handler():
    traps = {row["event_code"]: row for row in PAC_TRAPS}

    assert traps["PAC_ALARM_FIRED"]["trap_oid"] == "1.3.6.1.4.1.476.1.42.3.3.0.1"
    assert traps["PAC_ALARM_RESTORED"]["trap_oid"] == "1.3.6.1.4.1.476.1.42.3.3.0.2"
    assert all(is_pac_confirmation_trap(code) for code in traps)
    assert all(row["create_alert"] is False for row in PAC_TRAPS)
