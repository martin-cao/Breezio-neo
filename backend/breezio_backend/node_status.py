from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional


def node_online_ttl_seconds() -> int:
    return int(os.getenv("BREEZIO_NODE_ONLINE_TTL_SECONDS", "300"))


def effective_node_status(node: dict[str, Any], now: Optional[datetime] = None) -> str:
    status = str(node.get("status") or "offline")
    if status != "online":
        return status

    last_seen = _parse_timestamp(node.get("last_seen_at"))
    if last_seen is None:
        return "offline"

    current = now or datetime.now(timezone.utc)
    if (current - last_seen).total_seconds() > node_online_ttl_seconds():
        return "offline"
    return "online"


def apply_effective_node_status(node: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    return {**node, "status": effective_node_status(node, now)}


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
