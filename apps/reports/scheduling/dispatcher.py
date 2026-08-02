from __future__ import annotations

import logging

from django.db import models, transaction
from django.utils import timezone

from apps.reports.models import ReportSchedule, ReportScheduleStatus
from apps.reports.services.jobs import ReportJobService

logger = logging.getLogger(__name__)


def dispatch_due_report_schedules(limit: int = 100):
    now = timezone.now()
    queued_jobs = []
    with transaction.atomic():
        due_schedules = (
            ReportSchedule.objects.select_for_update(skip_locked=True)
            .filter(status=ReportScheduleStatus.ACTIVE, next_run_at__lte=now)
            .filter(models.Q(start_at__isnull=True) | models.Q(start_at__lte=now))
            .filter(models.Q(end_at__isnull=True) | models.Q(end_at__gte=now))
            .order_by("next_run_at", "created_at")[:limit]
        )
        for schedule in due_schedules:
            scheduled_for = schedule.next_run_at or now
            try:
                job = ReportJobService.create_scheduled_job(
                    schedule=schedule,
                    scheduled_for=scheduled_for,
                    enqueue=False,
                )
                queued_jobs.append(job)
                schedule.last_run_at = scheduled_for
                schedule.next_run_at = schedule.calculate_next_run_at(reference_time=scheduled_for)
                if schedule.end_at and schedule.next_run_at and schedule.next_run_at > schedule.end_at:
                    schedule.status = ReportScheduleStatus.EXPIRED
                    schedule.is_active = False
                schedule.save(update_fields=["last_run_at", "next_run_at", "status", "is_active", "updated_at"])
            except Exception:
                logger.exception("Failed to dispatch report schedule=%s", schedule.pk)
    return queued_jobs
