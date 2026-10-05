from apps.telemetry.models import MetricDataType
from apps.devices.management.commands.seed_onebank_liebert_pac_metrics import PAC_METRICS


def test_pac_compressor_status_metrics_match_frontend_codes_and_live_oids():
    mappings = {item["code"]: item for item in PAC_METRICS}

    assert mappings["pac_compressor_1_status"]["oid"] == (
        "1.3.6.1.4.1.476.1.42.3.9.20.1.20.1.2.1.5264.1"
    )
    assert mappings["pac_compressor_2_status"]["oid"] == (
        "1.3.6.1.4.1.476.1.42.3.9.20.1.20.1.2.1.5264.2"
    )
    assert mappings["pac_compressor_1_status"]["data_type"] == MetricDataType.BOOLEAN
    assert mappings["pac_compressor_2_status"]["data_type"] == MetricDataType.BOOLEAN
