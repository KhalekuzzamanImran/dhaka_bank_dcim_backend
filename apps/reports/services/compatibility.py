from __future__ import annotations

from apps.reports.models import ReportJob, ReportSchedule


def sync_legacy_job_file(job: ReportJob):
    if job.primary_artifact and job.primary_artifact.file:
        job.file = job.primary_artifact.file
        job.save(update_fields=["file", "updated_at"])
    return job


def sync_legacy_schedule_fields(schedule: ReportSchedule):
    schedule._sync_legacy_fields()
    schedule.save()
    return schedule
