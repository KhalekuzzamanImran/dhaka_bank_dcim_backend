from __future__ import annotations

from collections.abc import Iterable
import hashlib
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.utils import timezone

LIVE_UPDATES_REVISION_KEY = "live-updates:revision"
LIVE_UPDATES_LAST_EVENT_KEY = "live-updates:last-event"
LIVE_UPDATES_GROUP_NAME = "live-updates"
LIVE_UPDATES_GROUP_PREFIX = "live-updates-scope-"
# Only explicitly classified non-sensitive events may use this audience.
GLOBAL_EVENT_TYPES = frozenset()


def live_update_group_name(scope: str) -> str:
    """Return a Channels-safe, deterministic group name for an access scope."""
    digest = hashlib.sha256(str(scope).encode("utf-8")).hexdigest()[:32]
    return f"{LIVE_UPDATES_GROUP_PREFIX}{digest}"


def _current_revision() -> int:
    try:
        value = cache.get(LIVE_UPDATES_REVISION_KEY)
    except Exception:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _next_revision() -> int:
    try:
        if cache.add(LIVE_UPDATES_REVISION_KEY, 1, None):
            return 1
        return int(cache.incr(LIVE_UPDATES_REVISION_KEY))
    except Exception:
        return 1


def _normalize_scopes(scope: str = "global", scopes: Iterable[str] | None = None) -> list[str]:
    raw_scopes = list(scopes or [])
    if not raw_scopes:
        raw_scopes = [scope]
    normalized = []
    seen = set()
    for value in raw_scopes:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    if not normalized:
        normalized.append("global")
    return normalized


def telemetry_delta_from_latest(latest, *, observed_at=None) -> dict:
    """Serialize one persisted LatestTelemetry row without losing its value type."""
    metric = getattr(latest, "metric", None)
    device = getattr(latest, "device", None)
    device_type = getattr(device, "device_type", None)
    return {
        "device_id": str(getattr(latest, "device_id", getattr(device, "pk", ""))),
        "organization_id": str(getattr(latest, "organization_id", "") or ""),
        "data_center_id": str(getattr(latest, "data_center_id", "") or ""),
        "device_type": str(getattr(device_type, "code", "") or "").upper(),
        "metric_code": str(getattr(metric, "code", "") or ""),
        "value_float": latest.value_float,
        "value_integer": latest.value_integer,
        "value_boolean": latest.value_boolean,
        "value_text": latest.value_text,
        "quality": str(getattr(latest, "quality", "UNKNOWN") or "UNKNOWN"),
        "observed_at": observed_at.isoformat() if observed_at else None,
        "last_seen_at": latest.last_seen_at.isoformat() if latest.last_seen_at else None,
        "source": latest.source,
    }


def build_live_update_event(*, event_type: str, resource_type: str, resource_id: Any = None, scope: str = "global", scopes: Iterable[str] | None = None, metadata: dict | None = None) -> dict:
    scope_list = _normalize_scopes(scope=scope, scopes=scopes)
    revision = _next_revision()
    event = {
        "revision": revision,
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id is not None else None,
        "scope": scope_list[0],
        "scopes": scope_list,
        "timestamp": timezone.now().isoformat(),
        "metadata": metadata or {},
    }
    try:
        cache.set(LIVE_UPDATES_LAST_EVENT_KEY, event, None)
    except Exception:
        pass
    return event


def publish_live_update(*, event_type: str, resource_type: str, resource_id: Any = None, scope: str = "global", scopes: Iterable[str] | None = None, metadata: dict | None = None, delivery_scopes: Iterable[str] | None = None) -> dict:
    event = build_live_update_event(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        scope=scope,
        scopes=scopes,
        metadata=metadata,
    )

    try:
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            # Telemetry callers provide organization/data-center/device audience
            # scopes. Other existing event types retain the legacy global group
            # until their audience model is migrated independently.
            audience = list(delivery_scopes or [])
            if not audience:
                audience = [
                    value for value in event["scopes"]
                    if value.startswith(("organization:", "data_center:", "device:"))
                ]
            if not audience and event_type not in GLOBAL_EVENT_TYPES:
                return event
            audience = audience or ["global"]
            message = {"type": "live.update", "event": event}
            for audience_scope in set(audience):
                async_to_sync(channel_layer.group_send)(
                    live_update_group_name(audience_scope),
                    message,
                )
    except Exception:
        pass
    return event


def get_live_update_snapshot() -> dict:
    try:
        last_event = cache.get(LIVE_UPDATES_LAST_EVENT_KEY)
    except Exception:
        last_event = None
    return {
        "revision": _current_revision(),
        "last_event": last_event,
    }
