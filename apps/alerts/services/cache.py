"""Cache helpers for alert rule matching."""

from __future__ import annotations

import logging
import uuid

from django.core.cache import cache

logger = logging.getLogger(__name__)

ALERT_RULE_MATCH_CACHE_VERSION_KEY = "alert:matching-rules:version"
ALERT_RULE_MATCH_CACHE_TIMEOUT_SECONDS = 60


def get_alert_rule_match_cache_version() -> str:
    """Return the current alert-rule cache namespace version.

    A missing version is initialized lazily. If cache access fails, fall back to
    a local namespace so alert evaluation continues safely.
    """

    try:
        version = cache.get(ALERT_RULE_MATCH_CACHE_VERSION_KEY)
        if version:
            return str(version)

        version = uuid.uuid4().hex
        cache.set(ALERT_RULE_MATCH_CACHE_VERSION_KEY, version, timeout=None)
        return version
    except Exception:
        logger.warning("Alert rule cache version lookup failed; using fallback namespace.", exc_info=True)
        return "cache-unavailable"


def invalidate_alert_rule_match_cache() -> str | None:
    """Bump the alert-rule cache namespace.

    This avoids needing wildcard deletion and is safe across cache backends.
    """

    version = uuid.uuid4().hex
    try:
        cache.set(ALERT_RULE_MATCH_CACHE_VERSION_KEY, version, timeout=None)
        return version
    except Exception:
        logger.warning("Failed to invalidate alert rule matching cache.", exc_info=True)
        return None
