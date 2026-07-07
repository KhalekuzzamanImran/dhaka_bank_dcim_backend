from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.alerts.services.notifications import create_notifications_for_alert_opened
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.notifications.tasks import deliver_pending_notifications_task, send_notification_task
from apps.organizations.models import Organization


class NotificationHardeningTests(TestCase):
    def _org(self):
        return Organization.objects.create(name="Org", code="ORG-HARDEN")

    def _user(self, email="ops@example.com", phone="01700000000"):
        return User.objects.create_user(
            username=f"user-{email or phone}",
            password="test12345",
            email=email,
            phone=phone,
            is_active=True,
        )

    def _alert(self, severity="CRITICAL"):
        org = self._org()
        return SimpleNamespace(
            pk="alert-1",
            organization=org,
            organization_id=org.id,
            data_center_id=None,
            device=SimpleNamespace(name="PAC-01"),
            device_id="device-1",
            metric_id="metric-1",
            severity=severity,
            status="OPEN",
            message="PAC alarm confirmed",
            value_text=None,
            value_integer=1,
            value_float=None,
            value_boolean=None,
        )

    def test_alert_open_notifications_are_deduped(self):
        alert = self._alert()
        user = self._user()
        with patch("apps.alerts.services.notifications.get_alert_recipients", return_value=[user]), patch(
            "apps.alerts.services.notifications._queue_delivery", side_effect=lambda notification: notification
        ):
            created_first = create_notifications_for_alert_opened(alert)
            created_second = create_notifications_for_alert_opened(alert)

        self.assertEqual(len(created_first), 3)
        self.assertEqual(created_second, [])
        self.assertEqual(Notification.objects.filter(metadata__alert_event_id=str(alert.pk)).count(), 3)
        self.assertEqual(Notification.objects.filter(dedupe_key__isnull=False).count(), 3)

    def test_send_notification_task_noops_for_sent_rows(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Test",
            message="Hello",
            status=NotificationStatus.SENT,
        )

        result = send_notification_task.apply(args=[str(notification.id)]).get()

        self.assertEqual(result["status"], "sent")
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.SENT)

    def test_retry_pending_notifications_command_queues_old_rows(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="Pending",
            message="Pending message",
            status=NotificationStatus.PENDING,
        )
        Notification.objects.filter(id=notification.id).update(created_at=timezone.now() - timedelta(minutes=10))

        queued = []
        with patch("apps.notifications.services.delivery.queue_notification_delivery", side_effect=lambda row: queued.append(str(row.id)) or row):
            out = []

            class _Stdout:
                def write(self, msg):
                    out.append(str(msg))

            call_command("retry_pending_notifications", older_than_minutes=5, limit=10, stdout=_Stdout())

        self.assertIn(str(notification.id), queued)
        self.assertTrue(any("queued=1" in line for line in out))

    def test_deliver_pending_notifications_task_queues_old_rows(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Pending",
            message="Pending message",
            status=NotificationStatus.PENDING,
        )
        Notification.objects.filter(id=notification.id).update(created_at=timezone.now() - timedelta(minutes=10))

        queued = []
        with patch("apps.notifications.services.delivery.queue_notification_delivery", side_effect=lambda row: queued.append(str(row.id)) or row):
            result = deliver_pending_notifications_task.apply(kwargs={"limit": 10, "older_than_minutes": 5}).get()

        self.assertEqual(result["matched_count"], 1)
        self.assertIn(str(notification.id), queued)
