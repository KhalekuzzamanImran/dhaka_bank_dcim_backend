from unittest.mock import patch

from django.test import TestCase

from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceCredential, DeviceModel, DeviceProtocolConfig, DeviceType, ProtocolType, SNMPVersion, SNMPOIDMapping, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition, TelemetryPoint
from collectors.snmp_collector.client import SNMPResult
from collectors.snmp_collector.services import poll_snmp_device


class SnmpTelemetryIngestionTestCase(TestCase):
    def _build_device(self):
        org = Organization.objects.create(name="Org", code="ORG-SNMP")
        dc = DataCenter.objects.create(organization=org, name="DC", code="DC-SNMP")
        device_type = DeviceType.objects.create(name="Rack PDU", code="RACK_PDU", category="POWER")
        vendor = Vendor.objects.create(name="Schneider Electric / APC", code="APC")
        model = DeviceModel.objects.create(
            vendor=vendor,
            device_type=device_type,
            name="Schneider Electric / APC AP8853",
            model_number="AP8853",
        )
        device = Device.objects.create(
            organization=org,
            data_center=dc,
            device_type=device_type,
            device_model=model,
            name="Rack PDU 01",
            code="RACK-PDU-01",
            ip_address="10.10.10.20",
        )
        DeviceProtocolConfig.objects.create(
            device=device,
            protocol=ProtocolType.SNMP,
            host="10.10.10.20",
            port=161,
            timeout_seconds=5,
            retry_count=1,
            is_primary=True,
            is_enabled=True,
        )
        DeviceCredential.objects.create(
            device=device,
            protocol=ProtocolType.SNMP,
            username="",
            snmp_version=SNMPVersion.V2C,
            snmp_community_encrypted="public",
            is_active=True,
        )
        return device

    def _add_mapping(self, device, metric_code, metric_type, mapping_type, scale_factor, raw_oid="1.3.6.1.4.1.99999.1.1"):
        metric = MetricDefinition.objects.create(
            code=metric_code,
            name=metric_code.replace("_", " ").title(),
            category=MetricCategory.POWER if metric_type == MetricDataType.FLOAT else MetricCategory.STATUS,
            data_type=metric_type,
            unit="A" if metric_type == MetricDataType.FLOAT else None,
            is_active=True,
        )
        SNMPOIDMapping.objects.create(
            device_type=device.device_type,
            device_model=device.device_model,
            vendor=None,
            metric=metric,
            oid=raw_oid,
            data_type=mapping_type,
            scale_factor=scale_factor,
            offset_value=0,
            is_active=True,
        )
        return metric

    def _poll_with_raw_value(self, device, raw_value, raw_text=None):
        raw_text = raw_text if raw_text is not None else str(raw_value)
        with patch("collectors.snmp_collector.services.SNMPClient.get") as get_mock:
            get_mock.return_value = SNMPResult(oid="1.3.6.1.4.1.99999.1.1", value=raw_value, raw_value=raw_text)
            return poll_snmp_device(str(device.pk), evaluate_alerts=False)

    def test_snmp_integer_raw_scaled_to_float_metric_stores_value_float(self):
        device = self._build_device()
        metric = self._add_mapping(
            device,
            metric_code="pdu_bank1_current",
            metric_type=MetricDataType.FLOAT,
            mapping_type="integer",
            scale_factor="0.1",
        )

        result = self._poll_with_raw_value(device, 31)

        self.assertIn(result.status, {"SUCCESS", "PARTIAL_SUCCESS"})
        latest = LatestTelemetry.objects.get(device=device, metric=metric)
        point = TelemetryPoint.objects.get(device=device, metric=metric)
        self.assertEqual(latest.raw_value_text, "31")
        self.assertAlmostEqual(latest.value_float, 3.1)
        self.assertIsNone(latest.value_integer)
        self.assertIsNone(latest.value_boolean)
        self.assertIsNone(latest.value_text)
        self.assertEqual(point.raw_value_text, "31")
        self.assertAlmostEqual(point.value_float, 3.1)
        self.assertIsNone(point.value_integer)
        self.assertIsNone(point.value_boolean)
        self.assertIsNone(point.value_text)

    def test_snmp_integer_scale_stores_decimal_metric_float(self):
        device = self._build_device()
        metric = self._add_mapping(
            device,
            metric_code="pdu_energy",
            metric_type=MetricDataType.FLOAT,
            mapping_type="integer",
            scale_factor="0.01",
        )

        self._poll_with_raw_value(device, 98)

        latest = LatestTelemetry.objects.get(device=device, metric=metric)
        self.assertEqual(latest.raw_value_text, "98")
        self.assertAlmostEqual(latest.value_float, 0.98)
        self.assertIsNone(latest.value_integer)
        self.assertIsNone(latest.value_boolean)
        self.assertIsNone(latest.value_text)

    def test_snmp_integer_scale_one_stores_integer_metric_in_integer_column(self):
        device = self._build_device()
        metric = self._add_mapping(
            device,
            metric_code="pdu_bank1_count",
            metric_type=MetricDataType.INTEGER,
            mapping_type="integer",
            scale_factor="1",
        )

        self._poll_with_raw_value(device, 233)

        latest = LatestTelemetry.objects.get(device=device, metric=metric)
        self.assertEqual(latest.raw_value_text, "233")
        self.assertEqual(latest.value_integer, 233)
        self.assertIsNone(latest.value_float)
        self.assertIsNone(latest.value_boolean)
        self.assertIsNone(latest.value_text)

    def test_snmp_text_metric_stores_text_value_and_raw_text(self):
        device = self._build_device()
        metric = self._add_mapping(
            device,
            metric_code="pdu_model_number",
            metric_type=MetricDataType.TEXT,
            mapping_type="text",
            scale_factor="1",
        )

        self._poll_with_raw_value(device, "AP8853", raw_text="AP8853")

        latest = LatestTelemetry.objects.get(device=device, metric=metric)
        self.assertEqual(latest.raw_value_text, "AP8853")
        self.assertEqual(latest.value_text, "AP8853")
        self.assertIsNone(latest.value_float)
        self.assertIsNone(latest.value_integer)
        self.assertIsNone(latest.value_boolean)
