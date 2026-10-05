from apps.traps.management.commands.seed_onebank_socomec_ups_traps import _trap_rows


def test_onebank_socomec_seed_reads_all_canonical_netvision_v1_traps():
    rows = _trap_rows()

    assert len(rows) == 42
    assert rows[0]["trap_oid"] == "1.3.6.1.4.1.4555.1.1.7.2.0.1"
    assert rows[-1]["trap_oid"] == "1.3.6.1.4.1.4555.1.1.7.2.0.42"
    assert rows[0]["severity"] == "WARNING"
    assert rows[0]["create_alert"] is True
    assert rows[1]["severity"] == "INFO"
    assert rows[1]["create_alert"] is False


def test_restoration_traps_resolve_their_matching_alert_event_codes():
    rows = {row["event_name"]: row for row in _trap_rows()}

    assert rows["UPS Power Restored"]["resolves_event_code"] == "UPS_TRAP_ON_BATTERY"
    assert rows["UPS Communication Established"]["resolves_event_code"] == "UPS_TRAP_COMMUNICATION_LOST"
    assert rows["UPS EMD Temperature Restored from High"]["resolves_event_code"] == "UPS_TRAP_EMD_TEMP_HIGH"
