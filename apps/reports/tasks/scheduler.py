from __future__ import annotations

from apps.reports.scheduling.dispatcher import dispatch_due_report_schedules


def dispatch_due_report_schedules_task_impl(limit=100):
    return dispatch_due_report_schedules(limit=limit)
