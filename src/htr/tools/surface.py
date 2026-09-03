"""The v1 tool surface — six functions, frozen (SPEC).

Every handler here honors the latency line (SPEC R2): reads plus at most one cheap write.
dispatch_tool wraps every handler in the degrade-to-human posture (SPEC R3): any exception or
budget breach returns take_message semantics instead of a failure the caller can hear.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import sqlalchemy as sa

from .. import adapters
from ..config import Tenant
from ..db import messages, utcnow
from ..events import append_event, idempotency_key, record_action
from ..ports.voice import ToolCall

log = logging.getLogger("htr.tools")

BUDGET_SECONDS = 0.8
TOOLS_V1 = ("check_availability", "book_appointment", "take_message",
            "lookup_caller", "transfer_to_human", "send_link")


def _parse_day(arguments: dict[str, Any]) -> datetime:
    now = datetime.now(timezone.utc)
    raw = arguments.get("day") or arguments.get("date")
    if raw:
        try:
            d = datetime.fromisoformat(raw)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            # A model with a stale clock asks about the past (seen live: 2024 for "this
            # Friday" in 2026). Never offer history — snap to the same weekday ahead of now.
            if d.date() < now.date():
                days_ahead = (d.weekday() - now.weekday()) % 7
                d = (now + timedelta(days=days_ahead)).replace(
                    hour=0, minute=0, second=0, microsecond=0)
            return d
        except ValueError:
            pass
    return now + timedelta(days=1)


def _check_availability(engine, tenant: Tenant, tc: ToolCall) -> dict:
    cal = adapters.calendar_for(tenant, engine)
    day = _parse_day(tc.arguments)
    slots = cal.available_slots(day, limit=3)
    if not slots:
        return {"available": False, "slots": [],
                "speech": "I don't see anything open that day. Want me to take a message so "
                          f"{tenant.display_name} can find you a time?"}
    return {"available": True,
            "slots": [{"start": s.start.isoformat(), "end": s.end.isoformat()} for s in slots],
            "speech": "I have " + ", ".join(s.start.strftime("%I:%M %p").lstrip("0") for s in slots)
                      + ". Which works for you?"}


def _book_appointment(engine, tenant: Tenant, tc: ToolCall) -> dict:
    start_raw = tc.arguments.get("slot_start") or tc.arguments.get("start")
    if not start_raw:
        raise ValueError("book_appointment requires slot_start")
    start = datetime.fromisoformat(start_raw)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    from ..ports.calendar import Slot
    slot = Slot(start, start + timedelta(minutes=30))
    key = idempotency_key(tc.call_id, tc.tool_call_id, "book_appointment")
    with engine.begin() as conn:
        first = record_action(conn, tenant.id, key, "book_appointment",
                              {"slot_start": slot.start.isoformat(), "phone": tc.from_number})
    if not first:
        return {"booked": True, "duplicate": True,
                "speech": "You're already booked for that time — you're all set."}
    ref = adapters.calendar_for(tenant, engine).book(slot, tc.from_number,
                                                    tc.arguments.get("note", ""))
    return {"booked": True, "reference": ref,
            "speech": f"Done — you're booked for {slot.start.strftime('%A at %I:%M %p').replace(' 0', ' ')}."}


def _take_message(engine, tenant: Tenant, tc: ToolCall) -> dict:
    body = tc.arguments.get("message") or tc.arguments.get("body") or ""
    key = idempotency_key(tc.call_id, tc.tool_call_id, "take_message")
    with engine.begin() as conn:
        if record_action(conn, tenant.id, key, "take_message", {"body": body}):
            conn.execute(messages.insert().values(
                tenant_id=tenant.id, call_id=tc.call_id, from_number=tc.from_number,
                body=body, at=utcnow()))
    return {"taken": True,
            "speech": f"Got it. I'll make sure {tenant.display_name} sees that."}


def _lookup_caller(engine, tenant: Tenant, tc: ToolCall) -> dict:
    contact = adapters.contacts_for(tenant, engine).lookup(tenant.id, tc.from_number)
    if not contact:
        return {"known": False, "speech": ""}
    name = contact.get("name")
    return {"known": True, "name": name, "call_count": contact.get("call_count", 0),
            "speech": f"Welcome back{', ' + name if name else ''}."}


def _transfer_to_human(engine, tenant: Tenant, tc: ToolCall) -> dict:
    key = idempotency_key(tc.call_id, tc.tool_call_id, "transfer_to_human")
    with engine.begin() as conn:
        record_action(conn, tenant.id, key, "transfer_to_human",
                      {"to": tenant.escalation_phone})
    return {"transfer": True, "number": tenant.escalation_phone,
            "speech": "Let me connect you now — one moment."}


def _send_link(engine, tenant: Tenant, tc: ToolCall) -> dict:
    messenger = adapters.messaging_for(tenant)
    if not getattr(messenger, "can_text_callers", False):
        # voice-only number: don't pretend to text. Record the want; the post-call summary
        # carries it to the owner, and the caller gets a truthful path instead.
        with engine.begin() as conn:
            key = idempotency_key(tc.call_id, tc.tool_call_id, "send_link_declined")
            if record_action(conn, tenant.id, key, "take_message",
                             {"body": f"caller {tc.from_number} wanted the booking link texted"}):
                conn.execute(messages.insert().values(
                    tenant_id=tenant.id, call_id=tc.call_id, from_number=tc.from_number,
                    body="wanted the booking link texted", at=utcnow()))
        return {"sent": False,
                "speech": f"I can't text from this line yet — the link is {tenant.booking_link}, "
                          f"and I've left word so {tenant.display_name} can send it to you directly."}
    key = idempotency_key(tc.call_id, tc.tool_call_id, "send_link")
    with engine.begin() as conn:
        first = record_action(conn, tenant.id, key, "send_link",
                              {"to": tc.from_number, "link": tenant.booking_link})
    if first:
        messenger.send_sms(tc.from_number, f"{tenant.display_name}: {tenant.booking_link}")
    return {"sent": True, "speech": "Just texted it to you."}


_HANDLERS: dict[str, Callable] = {
    "check_availability": _check_availability,
    "book_appointment": _book_appointment,
    "take_message": _take_message,
    "lookup_caller": _lookup_caller,
    "transfer_to_human": _transfer_to_human,
    "send_link": _send_link,
}


def _degrade(engine, tenant: Tenant, tc: ToolCall, reason: str) -> dict:
    """SPEC R3: the caller never hears a failure. Record what we can, offer the human path."""
    log.warning("tool.degraded", extra={"tool": tc.name, "reason": reason, "call": tc.call_id})
    try:
        with engine.begin() as conn:
            append_event(conn, tenant.id, "tool.degraded",
                         {"tool": tc.name, "reason": reason}, call_id=tc.call_id)
            key = idempotency_key(tc.call_id, tc.tool_call_id, "degraded_message")
            if record_action(conn, tenant.id, key, "take_message",
                             {"body": f"[system] caller needed {tc.name}; degraded: {reason}"}):
                conn.execute(messages.insert().values(
                    tenant_id=tenant.id, call_id=tc.call_id, from_number=tc.from_number,
                    body=f"[system] caller needed {tc.name}; it was unavailable.", at=utcnow()))
    except Exception:
        log.exception("degrade path itself failed")
    return {"degraded": True,
            "speech": "I can't do that from here right now, but I've made a note and "
                      f"{tenant.display_name} will call you right back. Anything else?"}


def dispatch_tool(engine, tenant: Tenant, tc: ToolCall) -> dict:
    handler = _HANDLERS.get(tc.name)
    if handler is None:
        return _degrade(engine, tenant, tc, f"unknown tool {tc.name!r}")
    started = _time.monotonic()
    try:
        with engine.begin() as conn:
            append_event(conn, tenant.id, f"tool.{tc.name}", tc.arguments, call_id=tc.call_id)
        result = handler(engine, tenant, tc)
    except Exception as exc:                        # noqa: BLE001 — R3 catches everything
        return _degrade(engine, tenant, tc, repr(exc))
    elapsed = _time.monotonic() - started
    if elapsed > BUDGET_SECONDS:
        log.warning("tool.over_budget", extra={"tool": tc.name, "elapsed": elapsed})
    result.setdefault("elapsed_ms", int(elapsed * 1000))
    return result
