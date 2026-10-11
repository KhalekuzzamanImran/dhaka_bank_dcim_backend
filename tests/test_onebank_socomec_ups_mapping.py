from apps.devices.management.commands.seed_onebank_socomec_ups_metrics import UPS_METRICS


def test_socomec_ups_mappings_use_correct_output_load_column_and_scales():
    metrics = {item["code"]: item for item in UPS_METRICS}

    assert metrics["ups_output_l1_percent_load"]["oid"] == "1.3.6.1.2.1.33.1.4.4.1.5.1"
    assert metrics["ups_output_l2_percent_load"]["oid"] == "1.3.6.1.2.1.33.1.4.4.1.5.2"
    assert metrics["ups_output_l3_percent_load"]["oid"] == "1.3.6.1.2.1.33.1.4.4.1.5.3"
    assert metrics["ups_battery_voltage"]["scale_factor"] == "1"
    assert metrics["ups_input_l1_current"]["scale_factor"] == "1"
    assert metrics["ups_output_l1_current"]["scale_factor"] == "1"


def test_socomec_ups_mappings_include_bypass_readings_available_in_walk():
    metrics = {item["code"]: item for item in UPS_METRICS}

    assert metrics["ups_bypass_frequency"]["oid"] == "1.3.6.1.2.1.33.1.5.1.0"
    assert metrics["ups_bypass_l1_voltage"]["oid"] == "1.3.6.1.2.1.33.1.5.3.1.2.1"
    assert metrics["ups_bypass_l2_voltage"]["oid"] == "1.3.6.1.2.1.33.1.5.3.1.2.2"
    assert metrics["ups_bypass_l3_voltage"]["oid"] == "1.3.6.1.2.1.33.1.5.3.1.2.3"
