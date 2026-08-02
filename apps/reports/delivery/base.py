from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeliveryResult:
    accepted: bool
    delivered: bool
    provider_message_id: str | None = None
    provider_response: dict[str, Any] = field(default_factory=dict)
