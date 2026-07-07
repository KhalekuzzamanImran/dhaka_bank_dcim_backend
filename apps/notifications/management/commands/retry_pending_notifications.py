from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.services import queue_pending_notifications


class Command(BaseCommand):
    help = "Requeue old pending notifications to the Celery delivery task."

    def add_arguments(self, parser):
        parser.add_argument("--channel", action="append", default=[], choices=["EMAIL", "SMS", "WEB"], help="Filter by channel. Can be repeated.")
        parser.add_argument("--older-than-minutes", type=int, default=5, help="Only retry notifications older than this many minutes.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum notifications to queue.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be queued without sending tasks.")
        parser.add_argument("--ids", default="", help="Comma-separated notification IDs to target.")
        parser.add_argument("--include-failed", action="store_true", help="Also retry FAILED notifications.")

    def handle(self, *args, **options):
        channels = options["channel"] or []
        older_than_minutes = options["older_than_minutes"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        include_failed = options["include_failed"]
        id_values = [value.strip() for value in options["ids"].split(",") if value.strip()]
        qs = Notification.objects.filter(status=NotificationStatus.PENDING)
        if include_failed:
            qs = Notification.objects.filter(status__in=[NotificationStatus.PENDING, NotificationStatus.FAILED])
        if older_than_minutes is not None:
            from django.utils import timezone
            from datetime import timedelta

            cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
            qs = qs.filter(created_at__lte=cutoff)
        if channels:
            qs = qs.filter(channel__in=channels)
        if id_values:
            qs = qs.filter(id__in=id_values)

        qs = qs.order_by("created_at", "id")
        total_matched = qs.count()
        to_process = list(qs[:limit])

        if dry_run:
            for notification in to_process:
                self.stdout.write(
                    f"WOULD_QUEUE id={notification.id} channel={notification.channel} created_at={notification.created_at}"
                )
            self.stdout.write(
                self.style.SUCCESS(
                f"Retry pending notifications done. total_matched={total_matched} queued=0 dry_run=True"
                )
            )
            return

        for notification in to_process:
            self.stdout.write(f"QUEUEING id={notification.id} channel={notification.channel} created_at={notification.created_at}")

        matched, queued_notifications = queue_pending_notifications(
            limit=limit,
            older_than_minutes=older_than_minutes,
            channel=channels or None,
            ids=id_values or None,
            include_failed=include_failed,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Retry pending notifications done. total_matched={total_matched} queued={len(queued_notifications)} dry_run=False"
            )
        )
