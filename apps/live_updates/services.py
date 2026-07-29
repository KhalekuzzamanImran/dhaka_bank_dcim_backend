from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.utils import timezone

LIVE_UPDATES_REVISION_KEY = "live-updates:revision"
LIVE_UPDATES_LAST_EVENT_KEY = "live-updates:last-event"
LIVE_UPDATES_GROUP_NAME = "live-updates"


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


def publish_live_update(*, event_type: str, resource_type: str, resource_id: Any = None, scope: str = "global", scopes: Iterable[str] | None = None, metadata: dict | None = None) -> dict:
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
            async_to_sync(channel_layer.group_send)(
                LIVE_UPDATES_GROUP_NAME,
                {
                    "type": "live.update",
                    "event": event,
                },
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
