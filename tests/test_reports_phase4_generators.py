from __future__ import annotations

import os
import zipfile
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.alerts.models import AlertEvent, AlertSeverity, AlertStatus
from apps.audit.models import AuditAction, AuditLog
from apps.datacenters.models import DataCenter, Rack, Room
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.notifications.models import Notification, NotificationChannel, NotificationDelivery, NotificationStatus
from apps.organizations.models import Organization
from apps.reports.enums import ReportTriggerSource
from apps.reports.generators import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable, get_generator_class, get_supported_formats, register_generator
from apps.reports.models import ReportDefinition, ReportJob, ReportJobStatus, ReportTemplate
from apps.reports.services.factory import build_report_template_snapshot, build_scope_snapshot, create_report_job
from apps.reports.services.generator import generate_report_job
from apps.reports.services.definitions import get_active_definition_by_code
from apps.telemetry.models import MetricCategory, MetricDataType, MetricDefinition, TelemetryPoint


class ReportPhase4GeneratorTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="phase4-user", password="test12345", is_active=True)
        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.room = Room.objects.create(data_center=self.dc, name="Server Room", code="ROOM-1")
        self.rack = Rack.objects.create(data_center=self.dc, room=self.room, name="Rack 1", code="RACK-1")

        self.device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
        self.vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name="Model",
            model_number="M1",
        )
        self.device = Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            room=self.room,
            rack=self.rack,
            device_type=self.device_type,
            device_model=self.device_model,
            name="UPS-01",
            code="UPS-01",
            hostname="ups01.local",
            ip_address="10.0.0.10",
            status="ONLINE",
            is_active=True,
        )

        self._create_metric("room_temperature", "Room Temperature", unit="C")
        self._create_metric("room_humidity", "Room Humidity", unit="%")
        self._create_metric("roomTemp", "Room Temperature Legacy", unit="C")
        self._create_metric("roomRH", "Room Humidity Legacy", unit="%")
        self._create_metric("pac_room_temperature", "PAC Room Temperature", unit="C")
        self._create_metric("pac_room_humidity", "PAC Room Humidity", unit="%")
        self._create_metric("ups_load_percent", "UPS Load Percent", unit="%")
        self._create_metric("ups_battery_charge", "UPS Battery Charge", unit="%")

        self.audit_log = AuditLog.objects.create(
            organization=self.org,
            actor=self.user,
            action=AuditAction.REPORT_GENERATED,
            resource_type="ReportJob",
            resource_id="phase4",
            message="Phase 4 audit row",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )

    def _create_metric(self, code, name, *, unit=""):
        return MetricDefinition.objects.create(
            code=code,
            name=name,
            category=MetricCategory.ENVIRONMENT if "room" in code.lower() else MetricCategory.POWER,
            data_type=MetricDataType.FLOAT,
            unit=unit,
            is_active=True,
        )

    def _definition(self, code):
        return ReportDefinition.objects.get(code=code)

    def _template(
        self,
        *,
        code,
        definition,
        report_type,
        output_format="csv",
        primary_format=None,
        default_parameters=None,
        attachment_formats=None,
        include_raw_data=False,
    ):
        return ReportTemplate.objects.create(
            organization=self.org,
            definition=definition,
            name=code.replace("_", " ").title(),
            code=code,
            description="Phase 4 test template",
            config={
                "report_type": report_type,
                "output_format": output_format,
                "allowed_output_formats": [output_format],
            },
            default_parameters=default_parameters or {},
            primary_format=primary_format or output_format.upper(),
            attachment_formats=attachment_formats or [],
            include_raw_data=include_raw_data,
            is_active=True,
        )

    def _inventory_job(self, template):
        return create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={},
            queue_job=False,
        ).job

    def test_generator_registry_rejects_duplicates_and_unknown_keys(self):
        class DuplicateGenerator(BaseReportGenerator):
            definition_code = "DUPLICATE"
            generator_key = "duplicate_test_key"
            supported_formats = ("CSV",)

            def build_dataset(self, context: GeneratorContext) -> ReportDataset:
                return ReportDataset(title="Duplicate", tables=[ReportTable(name="Data", columns=["value"], rows=[{"value": "x"}], primary=True)])

        register_generator("duplicate_test_key", DuplicateGenerator)
        with self.assertRaises(ValueError):
            register_generator("duplicate_test_key", type("OtherGenerator", (BaseReportGenerator,), {"definition_code": "OTHER", "generator_key": "duplicate_test_key"}))
        with self.assertRaises(LookupError):
            get_generator_class("missing_generator_key")

    def test_definition_registry_format_capabilities_match(self):
        for code in [
            "DEVICE_INVENTORY",
            "TELEMETRY_EXPORT",
            "ALERT_SUMMARY",
            "ALERT_DETAIL",
            "NOTIFICATION_DELIVERY",
            "AUDIT_EXPORT",
            "ENVIRONMENTAL_TREND",
            "UPS_PERFORMANCE",
        ]:
            definition = self._definition(code)
            self.assertEqual(set(value.upper() for value in definition.supported_formats), set(get_supported_formats(definition.generator_key)))

    def test_device_inventory_csv_preserves_legacy_columns(self):
        template = self._template(
            code="PHASE4_DEVICE_INVENTORY",
            definition=self._definition("DEVICE_INVENTORY"),
            report_type="device_inventory",
        )
        job = self._inventory_job(template)
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("device_id,organization,data_center,room,rack,device,code,hostname,ip_address,device_type,device_model,vendor,status,is_active,last_seen", content)
        self.assertIn("UPS-01", content)

    def test_audit_export_csv_includes_user_agent(self):
        template = self._template(
            code="PHASE4_AUDIT_EXPORT",
            definition=self._definition("AUDIT_EXPORT"),
            report_type="audit_export",
        )
        job = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={
                "date_from": (timezone.now() - timedelta(hours=2)).isoformat(),
                "date_to": (timezone.now() + timedelta(hours=2)).isoformat(),
            },
            queue_job=False,
        ).job
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("created_at,actor,action,resource_type,resource_id,organization,message,ip_address,user_agent", content)
        self.assertIn("pytest", content)

    def test_environment_export_csv_uses_legacy_columns(self):
        now = timezone.now()
        temperature_metric = MetricDefinition.objects.get(code="room_temperature")
        humidity_metric = MetricDefinition.objects.get(code="room_humidity")
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=temperature_metric,
            time=now - timedelta(minutes=30),
            value_float=24.6,
            raw_value_text="24.6",
            quality="GOOD",
            source="sensor",
        )
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=humidity_metric,
            time=now - timedelta(minutes=25),
            value_float=51.2,
            raw_value_text="51.2",
            quality="GOOD",
            source="sensor",
        )
        template = self._template(
            code="PHASE4_ROOM_ENVIRONMENT",
            definition=self._definition("ENVIRONMENTAL_TREND"),
            report_type="room_environment",
            default_parameters={"metrics": ["room_temperature", "room_humidity"]},
        )
        job = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={
                "metrics": ["room_temperature", "room_humidity"],
                "date_from": (now - timedelta(hours=2)).isoformat(),
                "date_to": (now + timedelta(hours=2)).isoformat(),
            },
            queue_job=False,
        ).job
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("room_name,room_code,device_name,device_code,metric_code", content)
        self.assertIn("Server Room", content)
        self.assertIn("24.6", content)
        self.assertIn("51.2", content)

    def test_xlsx_generation_creates_valid_workbook(self):
        template = self._template(
            code="PHASE4_DEVICE_INVENTORY_XLSX",
            definition=self._definition("DEVICE_INVENTORY"),
            report_type="device_inventory",
            output_format="xlsx",
            primary_format="XLSX",
        )
        job = self._inventory_job(template)
        generated = generate_report_job(job.id)
        with zipfile.ZipFile(generated.file.path, "r") as archive:
            names = set(archive.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet2.xml", names)

    def test_pdf_generation_creates_valid_pdf(self):
        template = self._template(
            code="PHASE4_DEVICE_INVENTORY_PDF",
            definition=self._definition("DEVICE_INVENTORY"),
            report_type="device_inventory",
            output_format="pdf",
            primary_format="PDF",
        )
        job = self._inventory_job(template)
        generated = generate_report_job(job.id)
        with generated.file.open("rb") as handle:
            content = handle.read()
        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertIn(b"Confidential - Dhaka Bank DCIM report", content)

    def test_generation_uses_snapshot_and_completed_jobs_are_not_regenerated(self):
        temperature_metric = MetricDefinition.objects.get(code="pac_room_temperature")
        humidity_metric = MetricDefinition.objects.get(code="pac_room_humidity")
        now = timezone.now()
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=temperature_metric,
            time=now - timedelta(minutes=20),
            value_float=18.1,
            raw_value_text="18.1",
            quality="GOOD",
            source="sensor",
        )
        TelemetryPoint.objects.create(
            organization=self.org,
            data_center=self.dc,
            device=self.device,
            metric=humidity_metric,
            time=now - timedelta(minutes=15),
            value_float=51.6,
            raw_value_text="51.6",
            quality="GOOD",
            source="sensor",
        )
        template = self._template(
            code="PHASE4_TELEMETRY_EXPORT",
            definition=self._definition("TELEMETRY_EXPORT"),
            report_type="telemetry_export",
            default_parameters={"metric_codes": ["roomTemp"]},
            primary_format="CSV",
        )
        job_result = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.MANUAL,
            parameters={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
            queue_job=False,
        )
        template.default_parameters = {"metric_codes": ["roomRH"]}
        template.save(update_fields=["default_parameters", "updated_at"])

        generated = generate_report_job(job_result.job.id)
        with generated.file.open("rb") as handle:
            content = handle.read().decode("utf-8")
        self.assertIn("pac_room_temperature", content)
        self.assertNotIn("pac_room_humidity", content)

        updated_at = generated.updated_at
        regenerated = generate_report_job(generated.id)
        self.assertEqual(regenerated.status, ReportJobStatus.COMPLETED)
        self.assertEqual(regenerated.file.name, generated.file.name)
        self.assertEqual(regenerated.updated_at, updated_at)

    def test_unsupported_definition_format_is_rejected(self):
        class CsvOnlyGenerator(BaseReportGenerator):
            definition_code = "PHASE4_CSV_ONLY"
            generator_key = "phase4_csv_only"
            supported_formats = ("CSV",)

            def build_dataset(self, context: GeneratorContext) -> ReportDataset:
                return ReportDataset(title="CSV Only", tables=[ReportTable(name="Data", columns=["value"], rows=[{"value": "ok"}], primary=True)])

        register_generator("phase4_csv_only", CsvOnlyGenerator)
        definition = ReportDefinition.objects.create(
            code="PHASE4_CSV_ONLY",
            name="Phase 4 CSV Only",
            description="Phase 4 test definition",
            category="INVENTORY",
            generator_key="phase4_csv_only",
            parameter_schema={"type": "object", "properties": {}},
            supported_formats=["CSV"],
            supported_delivery_channels=["EMAIL"],
            requires_telemetry=False,
            requires_data_center=False,
            is_system=False,
            is_active=True,
            version=1,
        )
        template = ReportTemplate.objects.create(
            organization=self.org,
            definition=definition,
            name="Phase 4 CSV Only Template",
            code="PHASE4_CSV_ONLY_TEMPLATE",
            description="Phase 4 test template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={},
            primary_format="CSV",
            attachment_formats=[],
            include_raw_data=False,
            is_active=True,
        )
        scope_snapshot = {
            "organization": {"id": str(self.org.id), "name": self.org.name, "code": self.org.code},
            "data_center": {"id": str(self.dc.id), "name": self.dc.name, "code": self.dc.code},
            "selected_rooms": [],
            "selected_racks": [],
            "selected_devices": [],
        }
        job = ReportJob.objects.create(
            organization=self.org,
            data_center=self.dc,
            definition=definition,
            template=template,
            requested_by=self.user,
            status=ReportJobStatus.PENDING,
            parameters={},
            parameters_snapshot={},
            template_snapshot=build_report_template_snapshot(template),
            output_config_snapshot={"definition_code": definition.code, "report_type": "device_inventory", "primary_format": "PDF", "attachment_formats": []},
            scope_snapshot=scope_snapshot,
            recipient_snapshot={},
            source_event_snapshot={},
            trigger_source=ReportTriggerSource.MANUAL,
        )

        generated = generate_report_job(job.id)
        self.assertEqual(generated.status, ReportJobStatus.FAILED)
        self.assertIn("does not support PDF output", generated.error_message)
