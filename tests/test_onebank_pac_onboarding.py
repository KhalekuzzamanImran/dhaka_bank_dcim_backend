from apps.devices.management.commands.onboard_onebank_pac import _walk_identity


def test_onebank_pac_walk_identity_is_normalized_and_has_uptime():
    identity = _walk_identity()

    assert identity["sys_object_id"] == "1.3.6.1.4.1.476.1.42"
    assert identity["sys_descr"] == "Uninitialized"
    assert identity["sys_uptime_ticks"] == 32960044
