import struct
from decimal import Decimal
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from collectors.modbus_collector.reverse_gateway import (
    ModbusGatewayProtocolError,
    ModbusGatewayRemoteError,
    build_read_request,
    read_mapping,
    apply_battery_soh_fallback,
)
from apps.telemetry.models import TelemetryQuality


class FakeSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = []

    def sendall(self, payload):
        self.sent.append(payload)

    def recv(self, size):
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk


def make_mapping(**overrides):
    fields = {
        "slave_id": 1,
        "function_code": 3,
        "register_address": 10001,
        "register_count": 1,
        "data_type": "uint16",
        "byte_order": "BIG",
        "word_order": "BIG",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class ModbusReverseGatewayProtocolTests(TestCase):
    def test_uses_estimated_soh_fallback_only_for_zero_battery_soh(self):
        value, quality = apply_battery_soh_fallback("battery_soh", 0)

        self.assertEqual(value, Decimal("84.4"))
        self.assertEqual(quality, TelemetryQuality.ESTIMATED)

    def test_preserves_nonzero_soh_and_other_metrics(self):
        self.assertEqual(
            apply_battery_soh_fallback("battery_soh", 81.2),
            (81.2, TelemetryQuality.GOOD),
        )
        self.assertEqual(
            apply_battery_soh_fallback("battery_soc", 0),
            (0, TelemetryQuality.GOOD),
        )

    def test_builds_standard_modbus_tcp_read_request(self):
        mapping = make_mapping()

        request = build_read_request(23, mapping)

        self.assertEqual(request, struct.pack(">HHHBBHH", 23, 0, 6, 1, 3, 10001, 1))

    def test_reads_register_response_and_decodes_signed_values(self):
        mapping = make_mapping(data_type="int16")
        response = struct.pack(">HHHB", 23, 0, 5, 1) + bytes((3, 2)) + struct.pack(">H", 0xFF85)
        conn = FakeSocket(response)

        value = read_mapping(conn, mapping, 23)

        self.assertEqual(value, -123)
        self.assertEqual(conn.sent, [build_read_request(23, mapping)])

    def test_reads_one_register_for_unsigned_16_bit_soh_mapping(self):
        mapping = make_mapping(register_address=11131, register_count=1)
        response = struct.pack(">HHHB", 23, 0, 5, 1) + bytes((3, 2)) + struct.pack(">H", 775)

        value = read_mapping(FakeSocket(response), mapping, 23)

        self.assertEqual(value, 775)
        self.assertEqual(build_read_request(23, mapping), struct.pack(">HHHBBHH", 23, 0, 6, 1, 3, 11131, 1))

    def test_rejects_response_for_wrong_transaction(self):
        response = struct.pack(">HHHB", 24, 0, 5, 1) + bytes((3, 2)) + struct.pack(">H", 100)

        with self.assertRaises(ModbusGatewayProtocolError):
            read_mapping(FakeSocket(response), make_mapping(), 23)

    def test_reports_remote_modbus_exception(self):
        response = struct.pack(">HHHB", 23, 0, 3, 1) + bytes((0x83, 2))

        with self.assertRaises(ModbusGatewayRemoteError):
            read_mapping(FakeSocket(response), make_mapping(), 23)
