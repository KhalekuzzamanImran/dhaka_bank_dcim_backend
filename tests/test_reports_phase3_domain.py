from __future__ import annotations

from datetime import timedelta, time
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.organizations.models import Organization
from apps.reports.definition_seeds import REPORT_DEFINITION_SEEDS
from apps.reports.enums import ReportDefinitionCategory, ReportRecipientChannel, ReportTriggerSource
from apps.reports.models import ReportDefinition, ReportJob, ReportSchedule, ReportScheduleRecipient, ReportScheduleStatus
from apps.reports.services.definitions import (
    get_active_definition_by_code,
    seed_report_definitions,
    validate_definition_request,
)
from apps.reports.services.factory import create_report_job
from apps.reports.services.templates import create_report_template, update_report_template


class ReportPhase3DomainTestCase(TestCase):
    def setUp(self):
        seed_report_definitions()
        self.org = Organization.objects.create(name="Org One", code="ORG-1")
        self.other_org = Organization.objects.create(name="Org Two", code="ORG-2")
        self.dc = DataCenter.objects.create(organization=self.org, name="DC One", code="DC-1")
        self.other_dc = DataCenter.objects.create(organization=self.other_org, name="DC Two", code="DC-2")
        self.actor = User.objects.create_user(username="phase3-actor", password="test12345")
        self.actor.is_staff = True
        self.actor.is_superuser = True
        self.actor.save(update_fields=["is_staff", "is_superuser"])
        self.other_user = User.objects.create_user(username="phase3-other", password="test12345")

    def _template(self, **kwargs):
        definition = kwargs.pop("definition", ReportDefinition.objects.get(code="DEVICE_INVENTORY"))
        return create_report_template(
            actor=self.actor,
            organization=kwargs.pop("organization", self.org),
            code=kwargs.pop("code", "TPL-1"),
            name=kwargs.pop("name", "Template One"),
            description=kwargs.pop("description", "Test template"),
            config=kwargs.pop("config", {"report_type": "device_inventory", "output_format": "csv"}),
            default_parameters=kwargs.pop("default_parameters", {"status": "template-default"}),
            primary_format=kwargs.pop("primary_format", "CSV"),
            attachment_formats=kwargs.pop("attachment_formats", ["CSV"]),
            include_charts=kwargs.pop("include_charts", False),
            include_raw_data=kwargs.pop("include_raw_data", True),
            is_active=kwargs.pop("is_active", True),
            definition_code=definition.code,
        )

    def _schedule(self, *, template=None, status=ReportScheduleStatus.ACTIVE, parameters=None, overrides=None):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            template=template,
            name="Schedule One",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameters=parameters if parameters is not None else {"report_type": "device_inventory"},
            parameter_overrides=overrides if overrides is not None else {},
            recipients=["ops@example.com"],
            send_sms=False,
            sms_recipients=[],
            is_active=status == ReportScheduleStatus.ACTIVE,
            status=status,
            created_by=self.actor,
        )
        ReportScheduleRecipient.objects.create(
            schedule=schedule,
            channel=ReportRecipientChannel.EMAIL,
            recipient_type="EMAIL",
            email_address="ops@example.com",
        )
        return schedule

    def test_active_definition_lookup_and_inactive_rejection(self):
        active = get_active_definition_by_code("DEVICE_INVENTORY")
        self.assertIsNotNone(active)
        self.assertEqual(active.code, "DEVICE_INVENTORY")

        definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")
        definition.is_active = False
        definition.save(update_fields=["is_active"])
        self.assertIsNone(get_active_definition_by_code("DEVICE_INVENTORY"))

    def test_template_service_rejects_unauthorized_scope(self):
        with self.assertRaises(PermissionDenied):
            create_report_template(
                actor=self.other_user,
                organization=self.org,
                code="TPL-BLOCKED",
                name="Blocked",
                config={"report_type": "device_inventory", "output_format": "csv"},
                definition_code="DEVICE_INVENTORY",
            )

        with self.assertRaises(ValidationError):
            create_report_template(
                actor=self.actor,
                organization=self.org,
                data_center=self.other_dc,
                code="TPL-BLOCKED-DC",
                name="Blocked DC",
                config={"report_type": "device_inventory", "output_format": "csv"},
                definition_code="DEVICE_INVENTORY",
            )

    def test_definition_request_validation(self):
        definition = ReportDefinition.objects.create(
            code="CUSTOM_SCHEMA",
            name="Custom Schema",
            description="",
            category=ReportDefinitionCategory.TELEMETRY,
            generator_key="custom_schema",
            parameter_schema={
                "type": "object",
                "properties": {
                    "metric_id": {"type": "uuid"},
                    "mode": {"type": "string", "enum": ["A", "B"]},
                    "count": {"type": "integer", "minimum": 1, "maximum": 5},
                    "required_flag": {"type": "boolean"},
                },
                "required": ["metric_id", "mode", "count"],
            },
            supported_formats=["CSV"],
            supported_delivery_channels=["EMAIL"],
            requires_telemetry=True,
            requires_data_center=True,
            is_active=True,
        )

        normalized = validate_definition_request(
            definition,
            self.org,
            self.dc,
            {"metric_id": "d9b1f5d1-6a8d-4ed7-a2c1-876543210000", "mode": "A", "count": 3, "required_flag": True},
            "CSV",
            ["CSV"],
            ["EMAIL"],
        )
        self.assertEqual(normalized["parameters"]["mode"], "A")

        with self.assertRaises(ValidationError):
            validate_definition_request(definition, self.org, None, {"metric_id": "d9b1f5d1-6a8d-4ed7-a2c1-876543210000", "mode": "A", "count": 3}, "CSV", ["CSV"], ["EMAIL"])
        with self.assertRaises(ValidationError):
            validate_definition_request(definition, self.org, self.dc, {"metric_id": "not-a-uuid", "mode": "A", "count": 3}, "CSV", ["CSV"], ["EMAIL"])
        with self.assertRaises(ValidationError):
            validate_definition_request(definition, self.org, self.dc, {"metric_id": "d9b1f5d1-6a8d-4ed7-a2c1-876543210000", "mode": "Z", "count": 3}, "CSV", ["CSV"], ["EMAIL"])
        with self.assertRaises(ValidationError):
            validate_definition_request(definition, self.org, self.dc, {"metric_id": "d9b1f5d1-6a8d-4ed7-a2c1-876543210000", "mode": "A", "count": 3}, "PDF", ["CSV"], ["EMAIL"])
        with self.assertRaises(ValidationError):
            validate_definition_request(definition, self.org, self.dc, {"metric_id": "d9b1f5d1-6a8d-4ed7-a2c1-876543210000", "mode": "A", "count": 3}, "CSV", ["CSV"], ["SMS"])

    def test_template_service_version_increment_and_snapshot_preservation(self):
        template = self._template()
        original_version = template.version
        job = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory", "status": "template-default"},
            queue_job=False,
        ).job
        original_snapshot = job.template_snapshot

        updated = update_report_template(
            template,
            actor=self.actor,
            organization=self.org,
            name="Template One Updated",
            config={"report_type": "device_inventory", "output_format": "csv"},
        )
        self.assertEqual(updated.version, original_version + 1)
        job.refresh_from_db()
        self.assertEqual(job.template_snapshot["name"], original_snapshot["name"])
        self.assertEqual(job.template_snapshot["default_parameters"]["status"], "template-default")

    def test_parameter_merge_precedence_and_snapshot_immutability(self):
        template = self._template(default_parameters={"status": "template", "is_active": True, "device_id": "template-device"})
        schedule = self._schedule(template=template, overrides={"status": "schedule", "is_active": False, "device_id": "schedule-device"})
        explicit_parameters = {"status": "explicit", "is_active": True, "device_id": "explicit-device"}
        runtime_parameters = {"status": "runtime", "is_active": False, "device_id": "runtime-device", "window_start": "2026-08-01T00:00:00+06:00", "window_end": "2026-08-02T00:00:00+06:00"}

        result = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            schedule=schedule,
            trigger_source=ReportTriggerSource.SCHEDULED,
            requested_by=self.actor,
            parameters=explicit_parameters,
            runtime_parameters=runtime_parameters,
            scheduled_for=timezone.now() + timedelta(days=1),
            window_start=timezone.now(),
            window_end=timezone.now() + timedelta(days=1),
            queue_job=False,
        )
        job = result.job
        self.assertEqual(job.parameters["status"], "runtime")
        self.assertFalse(job.parameters["is_active"])
        self.assertEqual(job.parameters["device_id"], "runtime-device")
        self.assertEqual(job.parameters_snapshot["status"], "runtime")

        explicit_parameters["status"] = "mutated"
        runtime_parameters["status"] = "mutated-runtime"
        template.default_parameters["status"] = "mutated-template"
        job.refresh_from_db()
        self.assertEqual(job.parameters_snapshot["status"], "runtime")
        self.assertEqual(job.template_snapshot["default_parameters"]["status"], "template")

    def test_manual_and_event_idempotency(self):
        template = self._template()

        manual_first = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={},
            idempotency_key="manual-key",
            queue_job=False,
        )
        manual_second = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            idempotency_key="manual-key",
            queue_job=False,
        )
        other_user = User.objects.create_user(username="phase3-user-2", password="test12345")
        manual_third = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=other_user,
            parameters={"report_type": "device_inventory"},
            idempotency_key="manual-key",
            queue_job=False,
        )
        self.assertEqual(manual_first.job.pk, manual_second.job.pk)
        self.assertNotEqual(manual_first.job.pk, manual_third.job.pk)

        event_first = create_report_job(
            definition=template.definition,
            organization=self.org,
            template=template,
            trigger_source=ReportTriggerSource.EVENT,
            parameters={"report_type": "device_inventory"},
            source_event={"event_id": "event-1"},
            idempotency_key="event-key",
            queue_job=False,
        )
        event_second = create_report_job(
            definition=template.definition,
            organization=self.org,
            template=template,
            trigger_source=ReportTriggerSource.EVENT,
            parameters={"report_type": "device_inventory"},
            source_event={"event_id": "event-1"},
            idempotency_key="event-key",
            queue_job=False,
        )
        self.assertEqual(event_first.job.pk, event_second.job.pk)

    def test_idempotency_key_validation(self):
        template = self._template()
        null_key = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            idempotency_key=None,
            queue_job=False,
        )
        valid_key = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            idempotency_key="valid-key",
            queue_job=False,
        )
        self.assertIsNotNone(null_key.job.pk)
        self.assertIsNotNone(valid_key.job.pk)
        with self.assertRaises(ValidationError):
            create_report_job(
                definition=template.definition,
                organization=self.org,
                actor=self.actor,
                data_center=self.dc,
                template=template,
                trigger_source=ReportTriggerSource.MANUAL,
                requested_by=self.actor,
                parameters={"report_type": "device_inventory"},
                idempotency_key="",
                queue_job=False,
            )
        with self.assertRaises(ValidationError):
            create_report_job(
                definition=template.definition,
                organization=self.org,
                actor=self.actor,
                data_center=self.dc,
                template=template,
                trigger_source=ReportTriggerSource.MANUAL,
                requested_by=self.actor,
                parameters={"report_type": "device_inventory"},
                idempotency_key="   ",
                queue_job=False,
            )

    def test_scheduled_idempotency_and_legacy_schedule_compatibility(self):
        template = self._template()
        schedule = self._schedule(template=template)
        planned = timezone.now() + timedelta(days=1)

        first = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            schedule=schedule,
            trigger_source=ReportTriggerSource.SCHEDULED,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            scheduled_for=planned,
            window_start=planned - timedelta(hours=1),
            window_end=planned,
            queue_job=False,
        )
        duplicate = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            schedule=schedule,
            trigger_source=ReportTriggerSource.SCHEDULED,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            scheduled_for=planned,
            window_start=planned - timedelta(hours=1),
            window_end=planned,
            queue_job=False,
        )
        self.assertEqual(first.job.pk, duplicate.job.pk)
        self.assertEqual(schedule.runs.count(), 1)

        manual_run = create_report_job(
            definition=template.definition,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            template=template,
            schedule=schedule,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            queue_job=False,
        )
        self.assertNotEqual(first.job.pk, manual_run.job.pk)
        self.assertEqual(schedule.runs.count(), 2)

        legacy_schedule = self._schedule(template=None)
        legacy_result = create_report_job(
            definition=None,
            organization=self.org,
            actor=self.actor,
            data_center=self.dc,
            schedule=legacy_schedule,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=self.actor,
            parameters={"report_type": "device_inventory"},
            queue_job=False,
        )
        self.assertIsNone(legacy_result.job.definition_id)

    def test_on_commit_dispatch_and_rollback(self):
        template = self._template()
        with patch("apps.reports.tasks.generate_report_job_task.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                create_report_job(
                    definition=template.definition,
                    organization=self.org,
                    actor=self.actor,
                    data_center=self.dc,
                    template=template,
                    trigger_source=ReportTriggerSource.MANUAL,
                    requested_by=self.actor,
                    parameters={"report_type": "device_inventory"},
                    idempotency_key="commit-key",
                    queue_job=True,
                )
            self.assertGreaterEqual(len(callbacks), 1)
            self.assertTrue(mock_delay.called)

        with patch("apps.reports.tasks.generate_report_job_task.delay") as mock_delay:
            try:
                with transaction.atomic():
                    create_report_job(
                        definition=template.definition,
                        organization=self.org,
                        actor=self.actor,
                        data_center=self.dc,
                        template=template,
                        trigger_source=ReportTriggerSource.MANUAL,
                        requested_by=self.actor,
                        parameters={"report_type": "device_inventory"},
                        idempotency_key="rollback-key",
                        queue_job=True,
                    )
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
            self.assertFalse(mock_delay.called)

    def test_access_control_rejection(self):
        template = self._template()
        with self.assertRaises(PermissionDenied):
            create_report_job(
                definition=template.definition,
                organization=self.other_org,
                actor=self.other_user,
                data_center=self.other_dc,
                template=template,
                trigger_source=ReportTriggerSource.MANUAL,
                requested_by=self.other_user,
                parameters={"report_type": "device_inventory"},
                queue_job=False,
            )
