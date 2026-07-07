#!/usr/bin/env bash
set -euo pipefail

USERNAME="${1:-}"
EMAIL="${2:-test@example.com}"
PHONE="${3:-01700000000}"

docker compose exec -T api python manage.py shell <<PY
from django.utils import timezone
from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.notifications.tasks import send_notification_task

username = ${USERNAME@Q}
email = ${EMAIL@Q}
phone = ${PHONE@Q}

user = None
if username:
    user = User.objects.filter(username=username, is_active=True).first()

if user is None:
    user = User.objects.filter(is_active=True).order_by("date_joined").first()

if user is None:
    raise RuntimeError("No active user found")

updated_fields = []
if email:
    user.email = email
    updated_fields.append("email")

for field in ("phone", "mobile", "phone_number", "msisdn"):
    if hasattr(user, field):
        setattr(user, field, phone)
        updated_fields.append(field)
        break

if updated_fields:
    user.save()

print(f"Using user={user.username} email={getattr(user, 'email', None)} phone={getattr(user, 'phone', None)}")

notifications = [
    Notification.objects.create(
        organization_id=getattr(user, "organization_id", None),
        recipient=user,
        channel=NotificationChannel.EMAIL,
        subject="DCIM Test Email Notification",
        message="This is a test email notification from DCIM.",
        status=NotificationStatus.PENDING,
        metadata={"test": True, "channel": "EMAIL", "created_at": timezone.now().isoformat()},
    ),
    Notification.objects.create(
        organization_id=getattr(user, "organization_id", None),
        recipient=user,
        channel=NotificationChannel.SMS,
        subject="DCIM Test SMS Notification",
        message="This is a test SMS notification from DCIM.",
        status=NotificationStatus.PENDING,
        metadata={"test": True, "channel": "SMS", "created_at": timezone.now().isoformat()},
    ),
]

for notification in notifications:
    send_notification_task.delay(str(notification.id))
    print(f"Queued notification={notification.id} channel={notification.channel}")

print("Latest notification rows:")
for n in Notification.objects.order_by("-created_at")[:10]:
    print(n.id, n.channel, n.status, n.sent_at, n.error_message)
PY
