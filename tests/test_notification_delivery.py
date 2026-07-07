from types import SimpleNamespace
from datetime import timedelta
from io import StringIO

import pytest
from celery.exceptions import Retry
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.notifications.tasks import send_notification_task
from apps.notifications.tasks import deliver_pending_notifications_task
from apps.organizations.models import Organization
from apps.alerts.services.notifications import create_notifications_for_alert_opened, create_notifications_for_alert_resolved


def _notification_org():
    return Organization.objects.create(name="Org", code="ORG-NOTIF")


def _user(email="user@example.com", phone="01700000000"):
    return User.objects.create_user(
        username=f"user-{email or phone}",
        password="test12345",
        email=email,
        phone=phone,
        is_active=True,
    )


def _alert(severity="CRITICAL"):
    org = _notification_org()
    user_device = SimpleNamespace(name="PAC-01")
    return SimpleNamespace(
        pk="alert-1",
        organization=org,
        organization_id=org.id,
        data_center_id=None,
        device=user_device,
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


@pytest.mark.django_db
def test_web_notification_task_marks_sent():
    org = _notification_org()
    user = _user()
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.WEB,
        subject="Test",
        message="Hello",
        status=NotificationStatus.PENDING,
    )

    result = send_notification_task.apply(args=[str(notification.id)]).get()

    notification.refresh_from_db()
    assert result["status"] == "sent"
    assert notification.status == NotificationStatus.SENT
    assert notification.sent_at is not None


@pytest.mark.django_db
def test_email_notification_console_backend_marks_sent():
    org = _notification_org()
    user = _user(email="email@example.com")
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.EMAIL,
        subject="Email Test",
        message="Hello via email",
        status=NotificationStatus.PENDING,
    )

    result = send_notification_task.apply(args=[str(notification.id)]).get()

    notification.refresh_from_db()
    assert result["status"] == "sent"
    assert notification.status == NotificationStatus.SENT


@pytest.mark.django_db
def test_email_notification_missing_email_fails():
    org = _notification_org()
    user = _user(email="")
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.EMAIL,
        subject="Email Test",
        message="Hello via email",
        status=NotificationStatus.PENDING,
    )

    with pytest.raises(Retry):
        send_notification_task.apply(args=[str(notification.id)]).get()

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED
    assert "email" in (notification.error_message or "").lower()


@pytest.mark.django_db
def test_sms_notification_console_backend_marks_sent():
    org = _notification_org()
    user = _user(phone="01711111111")
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.SMS,
        subject="SMS Test",
        message="Hello via sms",
        status=NotificationStatus.PENDING,
    )

    result = send_notification_task.apply(args=[str(notification.id)]).get()

    notification.refresh_from_db()
    assert result["status"] == "sent"
    assert notification.status == NotificationStatus.SENT


@pytest.mark.django_db
def test_sms_notification_missing_phone_fails():
    org = _notification_org()
    user = _user(phone="")
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.SMS,
        subject="SMS Test",
        message="Hello via sms",
        status=NotificationStatus.PENDING,
    )

    with pytest.raises(Retry):
        send_notification_task.apply(args=[str(notification.id)]).get()

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED
    assert "phone" in (notification.error_message or "").lower()


@pytest.mark.django_db
def test_alert_open_creates_expected_channels_and_dedupes(monkeypatch):
    alert = _alert(severity="CRITICAL")
    user = _user(email="ops@example.com", phone="01722222222")
    monkeypatch.setattr(
        "apps.alerts.services.notifications.get_alert_recipients",
        lambda _alert: [user],
    )
    monkeypatch.setattr(
        "apps.alerts.services.notifications._queue_delivery",
        lambda notification: notification,
    )

    created_first = create_notifications_for_alert_opened(alert)
    created_second = create_notifications_for_alert_opened(alert)

    channels = sorted(n.channel for n in created_first)
    assert channels == [NotificationChannel.EMAIL, NotificationChannel.SMS, NotificationChannel.WEB]
    assert created_second == []
    assert Notification.objects.filter(metadata__alert_event_id=str(alert.pk)).count() == 3
    assert Notification.objects.filter(dedupe_key__isnull=False).count() == 3


@pytest.mark.django_db
def test_alert_resolved_creates_expected_channels_and_dedupes(monkeypatch):
    alert = _alert(severity="CRITICAL")
    alert.status = "RESOLVED"
    user = _user(email="ops@example.com", phone="01722222222")
    monkeypatch.setattr(
        "apps.alerts.services.notifications.get_alert_recipients",
        lambda _alert: [user],
    )
    monkeypatch.setattr(
        "apps.alerts.services.notifications._queue_delivery",
        lambda notification: notification,
    )

    created_first = create_notifications_for_alert_resolved(alert)
    created_second = create_notifications_for_alert_resolved(alert)

    channels = sorted(n.channel for n in created_first)
    assert channels == [NotificationChannel.EMAIL, NotificationChannel.WEB]
    assert created_second == []
    assert Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="RESOLVED").count() == 2


@pytest.mark.django_db
def test_send_notification_task_does_not_resend_when_already_sent():
    org = _notification_org()
    user = _user()
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.WEB,
        subject="Test",
        message="Hello",
        status=NotificationStatus.SENT,
    )

    result = send_notification_task.apply(args=[str(notification.id)]).get()

    assert result["status"] == "sent"
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.SENT


@pytest.mark.django_db
def test_retry_pending_notifications_command_queues_old_rows(monkeypatch):
    org = _notification_org()
    user = _user()
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
    monkeypatch.setattr(
        "apps.notifications.services.delivery.queue_notification_delivery",
        lambda row: queued.append(str(row.id)) or row,
    )
    out = StringIO()

    call_command("retry_pending_notifications", older_than_minutes=5, limit=10, stdout=out)

    assert str(notification.id) in queued
    assert "queued=1" in out.getvalue()


@pytest.mark.django_db
def test_retry_pending_notifications_dry_run_does_not_queue(monkeypatch):
    org = _notification_org()
    user = _user()
    notification = Notification.objects.create(
        organization=org,
        recipient=user,
        channel=NotificationChannel.SMS,
        subject="Pending",
        message="Pending message",
        status=NotificationStatus.PENDING,
    )
    Notification.objects.filter(id=notification.id).update(created_at=timezone.now() - timedelta(minutes=10))

    queued = []
    monkeypatch.setattr(
        "apps.notifications.services.delivery.queue_notification_delivery",
        lambda row: queued.append(str(row.id)) or row,
    )
    out = StringIO()

    call_command("retry_pending_notifications", older_than_minutes=5, limit=10, dry_run=True, stdout=out)

    assert queued == []
    assert "dry_run=True" in out.getvalue()


@pytest.mark.django_db
def test_deliver_pending_notifications_task_queues_old_rows(monkeypatch):
    org = _notification_org()
    user = _user()
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
    monkeypatch.setattr(
        "apps.notifications.services.delivery.queue_notification_delivery",
        lambda row: queued.append(str(row.id)) or row,
    )

    result = deliver_pending_notifications_task.apply(kwargs={"limit": 10, "older_than_minutes": 5}).get()

    assert result["matched_count"] == 1
    assert str(notification.id) in queued
