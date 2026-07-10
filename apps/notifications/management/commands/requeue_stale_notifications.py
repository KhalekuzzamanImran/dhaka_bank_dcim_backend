from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.notifications.models import NotificationChannel
from apps.notifications.services import requeue_stale_delivering_notifications


class Command(BaseCommand):
    help = "Requeue stale DELIVERING notifications back to PENDING."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=getattr(settings, "NOTIFICATION_DELIVERING_TIMEOUT_MINUTES", 10),
            help="Only requeue DELIVERING rows older than this many minutes.",
        )
        parser.add_argument("--limit", type=int, default=100, help="Maximum notifications to process.")
        parser.add_argument(
            "--channel",
            action="append",
            choices=NotificationChannel.values,
            default=[],
            help="Optional channel filter. Can be repeated.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Show what would be requeued without updating rows.")

    def handle(self, *args, **options):
        older_than_minutes = options["older_than_minutes"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        channels = options["channel"] or []

        matched, requeued = requeue_stale_delivering_notifications(
            older_than_minutes=older_than_minutes,
            limit=limit,
            channel=channels or None,
            dry_run=dry_run,
        )

        if dry_run:
            for notification in matched:
                self.stdout.write(
                    f"WOULD_REQUEUE id={notification.id} channel={notification.channel} updated_at={notification.updated_at}"
                )
        else:
            for notification in requeued:
                self.stdout.write(
                    f"REQUEUED id={notification.id} channel={notification.channel} updated_at={notification.updated_at}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Stale DELIVERING notifications done. matched={len(matched)} requeued={len(requeued)} dry_run={dry_run}"
            )
        )
