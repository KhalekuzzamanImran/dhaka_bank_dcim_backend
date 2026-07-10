"""Alert service facade.

The collectors import ``evaluate_latest`` from this package root. Keep that
stable while the actual engine lives in ``engine.py``.
"""

from .engine import (
    acknowledge_alert,
    create_alert_log,
    create_or_update_trap_alert,
    evaluate_latest,
    evaluate_many,
    get_matching_rules,
    manually_resolve_alert,
    open_alert,
    resolve_alert,
    suppress_alert,
    update_active_alert,
)
from .summary import (
    build_active_by_severity,
    build_alert_summary,
    build_dashboard_payload,
    build_recent_alerts,
    build_top_devices,
)

__all__ = [
    "acknowledge_alert",
    "build_active_by_severity",
    "build_alert_summary",
    "build_dashboard_payload",
    "build_recent_alerts",
    "build_top_devices",
    "create_alert_log",
    "create_or_update_trap_alert",
    "evaluate_latest",
    "evaluate_many",
    "get_matching_rules",
    "manually_resolve_alert",
    "open_alert",
    "resolve_alert",
    "suppress_alert",
    "update_active_alert",
]
