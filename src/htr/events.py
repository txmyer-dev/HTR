"""The spine (SPEC R4).

append_event: the only write path into events — append-only by construction.
record_action: idempotent side-effect gate. Returns False when the key already exists, which is
how a replayed webhook or duplicated tool call becomes a no-op instead of a double booking.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from .db import actions, events, utcnow


def append_event(conn: sa.Connection, tenant_id: str, kind: str,
                 payload: dict[str, Any] | None = None, call_id: str | None = None) -> None:
    conn.execute(events.insert().values(
        tenant_id=tenant_id, call_id=call_id, kind=kind, payload=payload or {}, at=utcnow()))


def idempotency_key(call_id: str, tool_call_id: str, kind: str) -> str:
    return f"{call_id}:{tool_call_id}:{kind}"


def record_action(conn: sa.Connection, tenant_id: str, key: str, kind: str,
                  payload: dict[str, Any] | None = None) -> bool:
    """Insert an action under its idempotency key. True if this is the first time."""
    try:
        with conn.begin_nested():
            conn.execute(actions.insert().values(
                tenant_id=tenant_id, idempotency_key=key, kind=kind,
                payload=payload or {}, at=utcnow()))
        return True
    except sa.exc.IntegrityError:
        return False
