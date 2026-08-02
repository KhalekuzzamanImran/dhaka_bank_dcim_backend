from __future__ import annotations


def cleanup_expired_report_artifacts_task_impl(limit=100):
    return {"cleaned": 0, "limit": limit}
