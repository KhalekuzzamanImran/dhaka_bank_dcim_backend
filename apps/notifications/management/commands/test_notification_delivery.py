from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.models import Notification, NotificationChannel, NotificationDelivery, NotificationStatus
from apps.notifications.tasks import send_notification_task
from apps.organizations.models import Organization


def _recipient_phone(user):
    for field in ("phone", "mobile", "phone_number", "msisdn"):
        value = getattr(user, field, None)
        if value:
            return str(value).strip(), field
    return None, None


class Command(BaseCommand):
    help = "Create one EMAIL and one SMS notification and queue them for delivery."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="", help="Preferred username to test against.")
        parser.add_argument("--email", default="test@example.com", help="Email to assign to the test user.")
        parser.add_argument("--phone", default="01700000000", help="Phone number to assign to the test user.")
        parser.add_argument(
            "--organization-code",
            default="",
            help="Organization code to attach notifications to. Defaults to the first active organization.",
        )
        parser.add_argument(
            "--wait",
            type=int,
            default=0,
            help="Optional number of seconds to poll for final delivery status after queueing.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        email = options["email"].strip()
        phone = options["phone"].strip()
        organization_code = options["organization_code"].strip()
        wait_seconds = options["wait"]

        user = None
        if username:
            user = User.objects.filter(username=username, is_active=True).first()
        if user is None:
            user = User.objects.filter(is_active=True).order_by("date_joined").first()
        if user is None:
            raise CommandError("No active user found")

        if email:
            user.email = email

        phone_field = None
        for field in ("phone", "mobile", "phone_number", "msisdn"):
            if hasattr(user, field):
                setattr(user, field, phone)
                phone_field = field
                break
        user.save()

        organization = None
        if organization_code:
            organization = Organization.objects.filter(code=organization_code, status="ACTIVE").first()
            if organization is None:
                raise CommandError(f"Organization not found for code={organization_code}")
        else:
            organization = Organization.objects.filter(status="ACTIVE").order_by("created_at").first()
            if organization is None:
                raise CommandError("No active organization found")

        self.stdout.write(
            self.style.SUCCESS(
                f"Using user={user.username} email={getattr(user, 'email', None)} "
                f"phone={getattr(user, phone_field, None) if phone_field else None} organization={organization.code}"
            )
        )

        notifications = []
        for channel, subject, message in (
            (
                NotificationChannel.EMAIL,
                "DCIM Test Email Notification",
                "This is a test email notification from DCIM.",
            ),
            (
                NotificationChannel.SMS,
                "DCIM Test SMS Notification",
                "This is a test SMS notification from DCIM.",
            ),
        ):
            notification = Notification.objects.create(
                organization=organization,
                recipient=user,
                subject=subject,
                message=message,
                metadata={
                    "test": True,
                    "channel": channel,
                    "created_at": timezone.now().isoformat(),
                    "created_by": "test_notification_delivery",
                },
            )
            if channel == NotificationChannel.EMAIL:
                recipient_address = getattr(user, "email", None)
            else:
                recipient_address = getattr(user, phone_field, None) if phone_field else None
            delivery = NotificationDelivery.objects.create(
                notification=notification,
                channel=channel,
                status=NotificationStatus.PENDING,
                recipient_address=recipient_address,
                metadata={
                    "test": True,
                    "channel": channel,
                    "created_at": timezone.now().isoformat(),
                    "created_by": "test_notification_delivery",
                },
            )
            notifications.append((notification, delivery))

        for notification, delivery in notifications:
            send_notification_task.delay(str(delivery.id))
            self.stdout.write(f"Queued notification={notification.id} delivery={delivery.id} channel={delivery.channel}")

        if wait_seconds > 0:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                statuses = list(
                    NotificationDelivery.objects.filter(id__in=[d.id for _, d in notifications]).values_list(
                        "id", "channel", "status", "error_message"
                    )
                )
                if all(status in {NotificationStatus.SENT, NotificationStatus.FAILED} for _, _, status, _ in statuses):
                    break
                time.sleep(1)

        self.stdout.write(self.style.SUCCESS("Latest delivery rows:"))
        for delivery in NotificationDelivery.objects.order_by("-created_at")[:10]:
            self.stdout.write(
                f"{delivery.id} {delivery.channel} {delivery.status} "
                f"{delivery.sent_at} {delivery.error_message or ''}"
            )
