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

__all__ = [
    "acknowledge_alert",
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
