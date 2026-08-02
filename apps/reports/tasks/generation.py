from __future__ import annotations

from apps.reports.services.execution import ReportExecutionService


def generate_report_job_task_impl(report_job_id):
    return ReportExecutionService.execute_job(report_job_id)
