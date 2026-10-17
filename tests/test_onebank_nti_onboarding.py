from django.test import SimpleTestCase

from apps.devices.management.commands.onboard_onebank_nti import (
    DEVICE_IP,
    DEVICE_TYPE_CODE,
    DIGITAL_INPUTS,
    EXTERNAL_SENSORS,
    INPUT_ALERT_STATUSES,
    INPUT_ALERT_RULES,
    MODEL_NUMBER,
    OID_MAPPINGS,
    PRODUCT_OID,
    OUTPUT_RELAYS,
    TRAP_MAPPINGS,
)


class OneBankNTIOnboardingDefinitionTests(SimpleTestCase):
    def test_device_identity_matches_live_verified_agent(self):
        self.assertEqual(DEVICE_IP, "10.156.0.155")
        self.assertEqual(DEVICE_TYPE_CODE, "NETBOTZ")
        self.assertEqual(MODEL_NUMBER, "ENVIROMUX-16D")
        self.assertEqual(PRODUCT_OID, "1.3.6.1.4.1.3699.1.1.11")

    def test_frontend_environment_external_sensor_relay_and_input_oids_are_seeded(self):
        mappings = {code: (oid, data_type, scale) for code, oid, data_type, scale in OID_MAPPINGS}
        self.assertEqual(mappings["netbotz_temperature"], (f"{PRODUCT_OID}.1.3.1.1.6.1", "integer", "0.1"))
        self.assertEqual(mappings["netbotz_humidity"], (f"{PRODUCT_OID}.1.3.1.1.6.2", "integer", "1"))
        self.assertEqual(mappings["netbotz_hardware_revision"][0], f"{PRODUCT_OID}.1.1.8.0")
        self.assertEqual(mappings["netbotz_device_uptime"][0], "1.3.6.1.2.1.1.3.0")
        self.assertEqual(len(DIGITAL_INPUTS), 8)
        self.assertEqual(len(mappings), 5 + len(DIGITAL_INPUTS) * 2 + len(EXTERNAL_SENSORS) * 6 + len(OUTPUT_RELAYS) * 2)
        self.assertEqual(
            mappings["netbotz_nti_digital_input_8_status"][0],
            f"{PRODUCT_OID}.1.6.1.1.8.8",
        )
        self.assertEqual(
            mappings["netbotz_nti_external_sensor_1_value"],
            (f"{PRODUCT_OID}.1.5.1.1.7.1", "integer", "0.1"),
        )
        self.assertEqual(
            mappings["netbotz_nti_external_sensor_17_status"][0],
            f"{PRODUCT_OID}.1.5.1.1.10.17",
        )
        self.assertEqual(
            mappings["netbotz_nti_output_relay_1_status"][0],
            f"{PRODUCT_OID}.1.8.1.1.3.1",
        )

    def test_trap_oids_follow_nti_sensor_notification_definitions(self):
        trap_oids = {item["trap_oid"] for item in TRAP_MAPPINGS}
        self.assertEqual(len(trap_oids), 6)
        self.assertIn(f"{PRODUCT_OID}.100.3.0.1", trap_oids)
        self.assertIn(f"{PRODUCT_OID}.100.6.0.1", trap_oids)
        self.assertIn(f"{PRODUCT_OID}.100.6.0.5", trap_oids)

    def test_each_input_status_alert_has_a_warning_rule(self):
        self.assertEqual(len(INPUT_ALERT_RULES), len(DIGITAL_INPUTS) * len(INPUT_ALERT_STATUSES))
        rules = {(item["metric_code"], item["status"]) for item in INPUT_ALERT_RULES}
        for index, _description, _normal_value in DIGITAL_INPUTS:
            metric_code = f"netbotz_nti_digital_input_{index}_status"
            for status, _status_label in INPUT_ALERT_STATUSES:
                self.assertIn((metric_code, status), rules)
