from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from rest_framework.test import APIClient
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.access_control.models import Permission, Role, RolePermission, RoleScope, UserResourceAccess
from apps.alerts.services.notifications import create_notifications_for_alert_opened
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.notifications.services import requeue_stale_delivering_notifications
from apps.notifications.tasks import deliver_pending_notifications_task, requeue_stale_delivering_notifications_task, send_notification_task
from apps.organizations.models import Organization


class NotificationHardeningTests(TestCase):
    def _org(self):
        return Organization.objects.create(name="Org", code="ORG-HARDEN")

    def _perm(self, code):
        return Permission.objects.get_or_create(
            code=code,
            defaults={"module": code.split(".")[0], "description": code},
        )[0]

    def _role(self, code="NOTIF_ROLE", name="Notification Role"):
        role, _ = Role.objects.update_or_create(
            code=code,
            defaults={"name": name, "scope": RoleScope.ORGANIZATION, "status": "ACTIVE"},
        )
        RolePermission.objects.get_or_create(role=role, permission=self._perm("notification.view"))
        return role

    def _grant_access(self, user, organization, role=None):
        role = role or self._role()
        return UserResourceAccess.objects.create(
            user=user,
            organization=organization,
            role=role,
            assigned_by=user,
            is_active=True,
        )

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

    def test_recent_delivering_notification_is_not_requeued(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
        )
        Notification.objects.filter(id=notification.id).update(updated_at=timezone.now() - timedelta(minutes=2))

        matched, requeued = requeue_stale_delivering_notifications(older_than_minutes=10, limit=10)

        self.assertEqual(matched, [])
        self.assertEqual(requeued, [])
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.DELIVERING)

    def test_stale_delivering_notification_is_reset_to_pending(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
            read_at=timezone.now(),
        )
        Notification.objects.filter(id=notification.id).update(updated_at=timezone.now() - timedelta(minutes=20))

        matched, requeued = requeue_stale_delivering_notifications(older_than_minutes=10, limit=10)

        self.assertEqual(len(matched), 1)
        self.assertEqual(len(requeued), 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.PENDING)
        self.assertEqual(notification.error_message, "Requeued after stale DELIVERING timeout")
        self.assertIsNotNone(notification.read_at)

    def test_sent_and_failed_notifications_are_not_touched(self):
        org = self._org()
        user = self._user()
        sent = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="Sent",
            message="Sent message",
            status=NotificationStatus.SENT,
        )
        failed = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="Failed",
            message="Failed message",
            status=NotificationStatus.FAILED,
        )
        Notification.objects.filter(id__in=[sent.id, failed.id]).update(updated_at=timezone.now() - timedelta(minutes=30))

        matched, requeued = requeue_stale_delivering_notifications(older_than_minutes=10, limit=10)

        self.assertEqual(matched, [])
        self.assertEqual(requeued, [])
        sent.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(sent.status, NotificationStatus.SENT)
        self.assertEqual(failed.status, NotificationStatus.FAILED)

    def test_dry_run_does_not_modify_stale_delivering_rows(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.SMS,
            subject="Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
            read_at=timezone.now(),
        )
        Notification.objects.filter(id=notification.id).update(updated_at=timezone.now() - timedelta(minutes=20))

        matched, requeued = requeue_stale_delivering_notifications(older_than_minutes=10, limit=10, dry_run=True)

        self.assertEqual(len(matched), 1)
        self.assertEqual(requeued, [])
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.DELIVERING)

    def test_channel_filter_applies_to_stale_delivering_rows(self):
        org = self._org()
        user = self._user()
        email_notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="Email Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
        )
        sms_notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.SMS,
            subject="SMS Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
        )
        Notification.objects.filter(id__in=[email_notification.id, sms_notification.id]).update(updated_at=timezone.now() - timedelta(minutes=20))

        matched, requeued = requeue_stale_delivering_notifications(older_than_minutes=10, limit=10, channel=NotificationChannel.EMAIL)

        self.assertEqual(len(matched), 1)
        self.assertEqual(len(requeued), 1)
        email_notification.refresh_from_db()
        sms_notification.refresh_from_db()
        self.assertEqual(email_notification.status, NotificationStatus.PENDING)
        self.assertEqual(sms_notification.status, NotificationStatus.DELIVERING)

    def test_requeue_stale_delivering_task_returns_counts(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
        )
        Notification.objects.filter(id=notification.id).update(updated_at=timezone.now() - timedelta(minutes=20))

        result = requeue_stale_delivering_notifications_task.apply(kwargs={"limit": 10, "older_than_minutes": 10}).get()

        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["requeued_count"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.PENDING)

    def test_requeue_stale_notifications_command_dry_run(self):
        org = self._org()
        user = self._user()
        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Delivering",
            message="Delivering message",
            status=NotificationStatus.DELIVERING,
        )
        Notification.objects.filter(id=notification.id).update(updated_at=timezone.now() - timedelta(minutes=20))

        out = []

        class _Stdout:
            def write(self, msg):
                out.append(str(msg))

        call_command("requeue_stale_notifications", older_than_minutes=10, limit=10, dry_run=True, stdout=_Stdout())

        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.DELIVERING)
        self.assertTrue(any("dry_run=True" in line for line in out))

    def test_unread_count_returns_only_current_users_unread_notifications(self):
        org = self._org()
        role = self._role()
        user = self._user(email="user1@example.com")
        other = self._user(email="user2@example.com")
        self._grant_access(user, org, role)
        self._grant_access(other, org, role)

        Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Unread 1",
            message="Message 1",
            status=NotificationStatus.SENT,
        )
        Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Read 1",
            message="Message 2",
            status=NotificationStatus.SENT,
            read_at=timezone.now(),
        )
        Notification.objects.create(
            organization=org,
            recipient=other,
            channel=NotificationChannel.WEB,
            subject="Unread 2",
            message="Message 3",
            status=NotificationStatus.SENT,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/api/v1/notifications/unread_count/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unread_count"], 1)

    def test_mark_read_marks_only_own_notification(self):
        org = self._org()
        role = self._role()
        user = self._user(email="user1@example.com")
        other = self._user(email="user2@example.com")
        self._grant_access(user, org, role)
        self._grant_access(other, org, role)

        my_notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="My Notification",
            message="Message 1",
            status=NotificationStatus.SENT,
        )
        other_notification = Notification.objects.create(
            organization=org,
            recipient=other,
            channel=NotificationChannel.WEB,
            subject="Other Notification",
            message="Message 2",
            status=NotificationStatus.SENT,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(f"/api/v1/notifications/{my_notification.id}/mark_read/")

        self.assertEqual(response.status_code, 200)
        my_notification.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertIsNotNone(my_notification.read_at)
        self.assertIsNone(other_notification.read_at)
        self.assertEqual(my_notification.status, NotificationStatus.SENT)

    def test_mark_all_read_marks_only_current_users_notifications(self):
        org = self._org()
        role = self._role()
        user = self._user(email="user1@example.com")
        other = self._user(email="user2@example.com")
        self._grant_access(user, org, role)
        self._grant_access(other, org, role)

        my_notification_1 = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="My Notification 1",
            message="Message 1",
            status=NotificationStatus.SENT,
        )
        my_notification_2 = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.EMAIL,
            subject="My Notification 2",
            message="Message 2",
            status=NotificationStatus.SENT,
        )
        other_notification = Notification.objects.create(
            organization=org,
            recipient=other,
            channel=NotificationChannel.WEB,
            subject="Other Notification",
            message="Message 3",
            status=NotificationStatus.SENT,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post("/api/v1/notifications/mark_all_read/")

        self.assertEqual(response.status_code, 200)
        my_notification_1.refresh_from_db()
        my_notification_2.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertIsNotNone(my_notification_1.read_at)
        self.assertIsNotNone(my_notification_2.read_at)
        self.assertIsNone(other_notification.read_at)

    def test_user_cannot_mark_another_users_notification_as_read(self):
        org = self._org()
        role = self._role()
        user = self._user(email="user1@example.com")
        other = self._user(email="user2@example.com")
        self._grant_access(user, org, role)
        self._grant_access(other, org, role)

        other_notification = Notification.objects.create(
            organization=org,
            recipient=other,
            channel=NotificationChannel.WEB,
            subject="Other Notification",
            message="Message 3",
            status=NotificationStatus.SENT,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(f"/api/v1/notifications/{other_notification.id}/mark_read/")

        self.assertEqual(response.status_code, 404)
        other_notification.refresh_from_db()
        self.assertIsNone(other_notification.read_at)

    def test_read_at_does_not_change_delivery_status(self):
        org = self._org()
        role = self._role()
        user = self._user(email="user1@example.com")
        self._grant_access(user, org, role)

        notification = Notification.objects.create(
            organization=org,
            recipient=user,
            channel=NotificationChannel.WEB,
            subject="Sent Notification",
            message="Message",
            status=NotificationStatus.SENT,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(f"/api/v1/notifications/{notification.id}/mark_read/")

        self.assertEqual(response.status_code, 200)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.SENT)
        self.assertIsNotNone(notification.read_at)
