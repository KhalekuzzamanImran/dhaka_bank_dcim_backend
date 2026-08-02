from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.notifications.services import _legacy_pending_queryset, _pending_delivery_queryset, queue_pending_notifications


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
        if dry_run:
            matched = list(
                _pending_delivery_queryset(
                    limit=limit,
                    older_than_minutes=older_than_minutes,
                    channel=channels or None,
                    ids=id_values or None,
                    include_failed=include_failed,
                )
            )
            if not matched:
                matched = list(
                    _legacy_pending_queryset(
                        limit=limit,
                        older_than_minutes=older_than_minutes,
                        channel=channels or None,
                        ids=id_values or None,
                        include_failed=include_failed,
                    )
                )
            queued_notifications = []
        else:
            matched, queued_notifications = queue_pending_notifications(
                limit=limit,
                older_than_minutes=older_than_minutes,
                channel=channels or None,
                ids=id_values or None,
                include_failed=include_failed,
            )

        if dry_run:
            for notification in matched:
                self.stdout.write(f"WOULD_QUEUE id={notification.id} channel={getattr(notification, 'channel', None)} created_at={notification.created_at}")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Retry pending notifications done. total_matched={len(matched)} queued=0 dry_run=True"
                )
            )
            return

        for notification in matched:
            self.stdout.write(f"QUEUEING id={notification.id} channel={getattr(notification, 'channel', None)} created_at={notification.created_at}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Retry pending notifications done. total_matched={len(matched)} queued={len(queued_notifications)} dry_run=False"
            )
        )
