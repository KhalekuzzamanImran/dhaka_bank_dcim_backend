from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from datetime import time
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.notifications.models import NotificationDelivery, NotificationStatus
from apps.organizations.models import Organization
from apps.reports.enums import ReportRecipientChannel, ReportTriggerSource
from apps.reports.models import (
    ReportDelivery,
    ReportDeliveryStatus,
    ReportDefinition,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleDelivery,
    ReportScheduleDeliveryStatus,
    ReportScheduleRecipient,
)
from apps.reports.services.deliveries import (
    create_report_deliveries_for_job,
    execute_report_delivery,
    queue_report_delivery,
    report_delivery_summary,
    retry_report_delivery,
)
from apps.reports.services.factory import create_report_job
from apps.reports.services.generator import generate_report_job


@override_settings(
    REPORT_EMAIL_ATTACHMENT_MAX_BYTES=5 * 1024 * 1024,
    REPORT_SMS_MAX_LENGTH=480,
)
class ReportPhase6ADeliveryTestCase(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="reports-phase6a-")
        self._override = override_settings(MEDIA_ROOT=self.media_root)
        self._override.enable()

        suffix = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(username=f"delivery-user-{suffix}", password="test12345", is_active=True)
        self.org = Organization.objects.create(name=f"Org {suffix}", code=f"ORG-{suffix}")
        self.dc = DataCenter.objects.create(organization=self.org, name=f"DC {suffix}", code=f"DC-{suffix}")
        self.device_type = DeviceType.objects.create(name="UPS", code=f"UPS-{suffix}", category="POWER")
        self.vendor = Vendor.objects.create(name="Vendor", code=f"VEN-{suffix}")
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            device_type=self.device_type,
            name="Model",
            model_number=f"M-{suffix}",
        )
        Device.objects.create(
            organization=self.org,
            data_center=self.dc,
            device_type=self.device_type,
            device_model=self.device_model,
            name="UPS-01",
            code=f"UPS-{suffix}",
        )
        self.definition = ReportDefinition.objects.get(code="DEVICE_INVENTORY")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _template(self, *, primary_format="CSV", attachment_formats=None):
        return self.definition.templates.create(
            organization=self.org,
            name="Delivery Template",
            code=f"TPL-{uuid.uuid4().hex[:8]}",
            description="Delivery test template",
            config={"report_type": "device_inventory", "output_format": "csv"},
            default_parameters={},
            primary_format=primary_format,
            attachment_formats=attachment_formats or [],
            include_charts=False,
            include_raw_data=False,
            is_active=True,
        )

    def _completed_job(self, *, template=None, schedule=None, recipient_snapshot=None, primary_format="CSV", attachment_formats=None):
        template = template or self._template(primary_format=primary_format, attachment_formats=attachment_formats)
        result = create_report_job(
            definition=template.definition,
            organization=self.org,
            data_center=self.dc,
            template=template,
            schedule=schedule,
            requested_by=self.user,
            trigger_source=ReportTriggerSource.SCHEDULED if schedule else ReportTriggerSource.MANUAL,
            parameters={"report_type": "device_inventory"},
            queue_job=False,
        )
        job = result.job
        if recipient_snapshot is not None:
            job.recipient_snapshot = recipient_snapshot
            job.save(update_fields=["recipient_snapshot", "updated_at"])
        with patch("apps.reports.services.execution._queue_report_deliveries", lambda job_id: None):
            generated = generate_report_job(job.id)
        self.assertEqual(generated.status, ReportJobStatus.COMPLETED)
        self.assertTrue(generated.artifacts.exists())
        return generated

    def test_create_report_deliveries_uses_snapshot_recipients_and_deduplicates(self):
        job = self._completed_job(
            recipient_snapshot={
                "email_recipients": [" ops@example.com ", "ops@example.com"],
                "sms_recipients": ["01329665857", " 01329665857 "],
                "send_sms": True,
            }
        )
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            result = create_report_deliveries_for_job(job=job)

        self.assertEqual(result["created_count"], 2)
        self.assertEqual(ReportDelivery.objects.filter(job=job).count(), 2)
        self.assertEqual(set(ReportDelivery.objects.filter(job=job).values_list("recipient", flat=True)), {"ops@example.com", "01329665857"})

    def test_structured_recipients_ignore_inactive_rows(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Structured schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameters={},
            recipients=["ops@example.com"],
            sms_recipients=[],
            status="ACTIVE",
            is_active=True,
        )
        ReportSchedule.objects.filter(pk=schedule.pk).update(recipients=[])
        ReportScheduleRecipient.objects.create(
            schedule=schedule,
            channel=ReportRecipientChannel.EMAIL,
            display_name="Ops",
            email_address="ops@example.com",
            is_active=False,
        )
        job = self._completed_job(schedule=schedule, recipient_snapshot={})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            result = create_report_deliveries_for_job(job=job)

        self.assertEqual(result["created_count"], 0)
        self.assertFalse(ReportDelivery.objects.filter(job=job).exists())

    def test_legacy_recipient_adapter_is_used_when_snapshot_is_empty(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Legacy schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameters={},
            recipients=[" ops@example.com ", "ops@example.com"],
            send_sms=True,
            sms_recipients=["01329665857", " 01329665857 "],
            status="ACTIVE",
            is_active=True,
        )
        job = self._completed_job(schedule=schedule, recipient_snapshot={})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            result = create_report_deliveries_for_job(job=job)

        self.assertEqual(result["created_count"], 2)
        self.assertEqual(ReportDelivery.objects.filter(job=job).count(), 2)

    def test_queue_then_execute_email_delivery_updates_report_delivery_and_notification_link(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "send_sms": False})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")

        queued = queue_report_delivery(delivery=delivery)
        self.assertEqual(queued.status, ReportDeliveryStatus.QUEUED)
        self.assertTrue(
            AuditLog.objects.filter(action="REPORT_DELIVERY_QUEUED", resource_type="ReportDelivery", resource_id=str(delivery.id)).exists()
        )

        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-123"},
        ) as send_mock:
            sent = execute_report_delivery(delivery_id=delivery.id)

        self.assertEqual(sent.status, ReportDeliveryStatus.SENT)
        self.assertEqual(sent.provider_message_id, "mail-123")
        self.assertEqual(sent.sent_at is not None, True)
        self.assertEqual(send_mock.call_count, 1)
        notification_delivery = NotificationDelivery.objects.get(report_delivery=sent)
        self.assertEqual(notification_delivery.status, NotificationStatus.SENT)
        self.assertEqual(notification_delivery.provider_message_id, "mail-123")
        self.assertTrue(
            AuditLog.objects.filter(action="REPORT_DELIVERY_SENT", resource_type="ReportDelivery", resource_id=str(sent.id)).exists()
        )

    def test_email_delivery_attaches_primary_artifact_when_within_limit(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "send_sms": False})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")

        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-attach"},
        ) as send_mock:
            execute_report_delivery(delivery_id=delivery.id)

        _, kwargs = send_mock.call_args
        self.assertIn("attachments", kwargs)
        attachments = kwargs["attachments"]
        self.assertTrue(attachments)
        self.assertTrue(attachments[0]["filename"].endswith(".csv"))
        self.assertEqual(attachments[0]["mimetype"], "text/csv")

    def test_email_delivery_uses_download_link_when_attachment_too_large(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "send_sms": False})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")

        with override_settings(REPORT_EMAIL_ATTACHMENT_MAX_BYTES=1), patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-link"},
        ) as send_mock:
            execute_report_delivery(delivery_id=delivery.id)

        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["attachments"], [])
        self.assertIn("/api/v1/reports/report-artifacts/", kwargs["body"])

    def test_sms_delivery_respects_length_limit_and_uses_no_attachment(self):
        job = self._completed_job(recipient_snapshot={"sms_recipients": ["01329665857"], "send_sms": True})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="SMS")

        with override_settings(REPORT_SMS_MAX_LENGTH=60), patch(
            "apps.notifications.services.delivery.send_sms_notification",
            return_value={"backend": "soap", "sms_csms_id": "sms-123"},
        ) as send_mock:
            execute_report_delivery(delivery_id=delivery.id)

        _, kwargs = send_mock.call_args
        self.assertIn("message", kwargs)
        self.assertLessEqual(len(kwargs["message"]), 60)
        delivery.refresh_from_db()
        self.assertEqual(delivery.provider_message_id, "sms-123")

    def test_provider_failure_and_retry_flow(self):
        job = self._completed_job(recipient_snapshot={"sms_recipients": ["01329665857"], "send_sms": True})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="SMS")

        with patch(
            "apps.notifications.services.delivery.send_sms_notification",
            side_effect=requests.ConnectionError("temporary network problem"),
        ):
            with self.assertRaises(requests.ConnectionError):
                execute_report_delivery(delivery_id=delivery.id)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ReportDeliveryStatus.QUEUED)
        self.assertEqual(delivery.retry_count, 1)

        with patch(
            "apps.notifications.services.delivery.send_sms_notification",
            return_value={"backend": "soap", "sms_csms_id": "sms-456"},
        ):
            sent = execute_report_delivery(delivery_id=delivery.id)

        self.assertEqual(sent.status, ReportDeliveryStatus.SENT)
        self.assertEqual(ReportDelivery.objects.get(pk=delivery.pk).provider_message_id, "sms-456")

    def test_permanent_provider_failure_marks_failed_and_can_be_retried_manually(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "send_sms": False})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")

        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            side_effect=ValueError("invalid email provider response"),
        ):
            with self.assertRaises(ValueError):
                execute_report_delivery(delivery_id=delivery.id)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ReportDeliveryStatus.FAILED)
        self.assertEqual(delivery.retry_count, 0)

        retried = retry_report_delivery(delivery=delivery, requested_by=self.user)
        self.assertEqual(retried.status, ReportDeliveryStatus.QUEUED)
        self.assertEqual(retried.retry_count, 1)

    def test_duplicate_delivery_execution_does_not_resend(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "send_sms": False})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")

        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-once"},
        ) as send_mock:
            execute_report_delivery(delivery_id=delivery.id)
            execute_report_delivery(delivery_id=delivery.id)

        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(ReportDelivery.objects.get(pk=delivery.pk).status, ReportDeliveryStatus.SENT)

    def test_mixed_outcomes_produce_partial_summary_and_job_remains_completed(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "sms_recipients": ["01329665857"], "send_sms": True})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)

        email_delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")
        sms_delivery = ReportDelivery.objects.get(job=job, channel="SMS")

        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-partial"},
        ):
            execute_report_delivery(delivery_id=email_delivery.id)

        with patch(
            "apps.notifications.services.delivery.send_sms_notification",
            side_effect=ValueError("sms rejected"),
        ):
            with self.assertRaises(ValueError):
                execute_report_delivery(delivery_id=sms_delivery.id)

        self.assertEqual(ReportDelivery.objects.get(pk=email_delivery.pk).status, ReportDeliveryStatus.SENT)
        self.assertEqual(ReportDelivery.objects.get(pk=sms_delivery.pk).status, ReportDeliveryStatus.FAILED)
        self.assertEqual(report_delivery_summary(job), "PARTIAL")
        job.refresh_from_db()
        self.assertEqual(job.status, ReportJobStatus.COMPLETED)

    def test_notification_delivery_link_is_persisted(self):
        job = self._completed_job(recipient_snapshot={"email_recipients": ["ops@example.com"], "send_sms": False})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")
        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-linked"},
        ):
            execute_report_delivery(delivery_id=delivery.id)

        notification_delivery = NotificationDelivery.objects.get(report_delivery=delivery)
        self.assertEqual(notification_delivery.status, NotificationStatus.SENT)
        self.assertEqual(notification_delivery.report_delivery_id, delivery.id)

    def test_schedule_compatibility_delivery_rows_are_kept_in_sync(self):
        schedule = ReportSchedule.objects.create(
            organization=self.org,
            data_center=self.dc,
            name="Compat schedule",
            report_type="device_inventory",
            frequency="DAILY",
            delivery_time=time(6, 0),
            output_format="CSV",
            parameters={},
            recipients=["ops@example.com"],
            sms_recipients=[],
            status="ACTIVE",
            is_active=True,
        )
        job = self._completed_job(schedule=schedule, recipient_snapshot={})
        with patch("apps.reports.services.deliveries.queue_report_delivery", side_effect=lambda delivery: delivery):
            create_report_deliveries_for_job(job=job)
        delivery = ReportDelivery.objects.get(job=job, channel="EMAIL")

        with patch(
            "apps.notifications.services.delivery.send_email_notification",
            return_value={"backend": "email", "sent_count": 1, "provider_message_id": "mail-legacy"},
        ):
            execute_report_delivery(delivery_id=delivery.id)

        legacy = ReportScheduleDelivery.objects.get(run__generated_job=job, channel="EMAIL")
        self.assertEqual(legacy.status, ReportScheduleDeliveryStatus.SENT)
        self.assertEqual(legacy.provider_message_id, "mail-legacy")
