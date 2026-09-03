"""Everything slow (STEALS: switchboard jobs.py, plus the loop's own handlers).

post_call  -> summary to the owner (kept), contact upsert, a draft job per message taken
draft      -> the first reader decides whether it waits; the drafter writes a proposal
digest     -> number what waits, text the owner
dispatch   -> a signed proposal goes out as the owner, once
expire     -> overdue drafts stop waiting; the counterparty is told; the item is re-listed
heartbeat  -> probe the adapters; page the operator, never the client

Job handlers are plain functions; enqueue() is the only way work crosses the latency line.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from . import adapters
from .config import Tenant, TenantRegistry
from .db import aware, calls, jobs, messages, utcnow
from .events import append_event, record_action
from .log import jsonl
from .ports.llm import DraftRequest
from .proposals.store import FAILED, SIGNED, ProposalStore
from .reader.deadline import find_deadline
from .reader.waiting import Msg, latest_unanswered
from .rulings.controller import Rulings

log = logging.getLogger("htr.jobs")

MAX_ATTEMPTS = 5


def enqueue(conn: sa.Connection, tenant_id: str, kind: str,
            payload: dict[str, Any] | None = None, run_after: datetime | None = None) -> None:
    conn.execute(jobs.insert().values(
        tenant_id=tenant_id, kind=kind, payload=payload or {},
        status="pending", attempts=0,
        run_after=run_after or utcnow(), created_at=utcnow()))


def enqueue_once(conn: sa.Connection, tenant_id: str, kind: str,
                 payload: dict[str, Any] | None = None) -> bool:
    """Enqueue unless a pending job of this kind already waits for the tenant. Two messages
    from one call should not produce two digests."""
    exists = conn.execute(sa.select(jobs.c.id).where(
        jobs.c.tenant_id == tenant_id, jobs.c.kind == kind, jobs.c.status == "pending")).first()
    if exists:
        return False
    enqueue(conn, tenant_id, kind, payload)
    return True


def make_rulings(engine) -> Rulings:
    store = ProposalStore(engine)

    def enqueue_dispatch(tenant_id: str, proposal_id: int) -> None:
        with engine.begin() as conn:
            enqueue(conn, tenant_id, "dispatch", {"proposal_id": proposal_id})

    return Rulings(engine, store, adapters.messaging_for, enqueue_dispatch)


# ---------------------------------------------------------------- handlers

def _post_call(engine, registry: TenantRegistry, tenant: Tenant, payload: dict) -> None:
    """Owner summary (if configured), contact upsert, and a draft job per message taken."""
    call_id = payload["call_id"]
    with engine.connect() as conn:
        call = conn.execute(sa.select(calls).where(calls.c.id == call_id)).mappings().first()
        msgs = conn.execute(sa.select(messages.c.id, messages.c.body).where(
            messages.c.call_id == call_id)).mappings().all()
    if call is None:
        raise RuntimeError(f"post_call for unknown call {call_id}")

    adapters.contacts_for(tenant, engine).upsert_from_call(
        tenant.id, call["from_number"], payload.get("caller_name"))

    if tenant.owner.summary_after_call:
        lines = [f"Call from {call['from_number']}: {call['outcome'] or 'completed'}."]
        if call["summary"]:
            lines.append(call["summary"])
        lines += [f"Message: {m['body']}" for m in msgs]
        adapters.messaging_for(tenant).send_sms(tenant.owner.phone, "\n".join(lines)[:1500])

    with engine.begin() as conn:
        append_event(conn, tenant.id, "post_call.done", {"messages": len(msgs)}, call_id=call_id)
        for m in msgs:
            if m["body"].startswith("[system]"):
                continue
            enqueue(conn, tenant.id, "draft", {"source_kind": "call_message", "message_id": m["id"]})


def _draft(engine, registry: TenantRegistry, tenant: Tenant, payload: dict) -> None:
    """The first reader, then the drafter. Idempotent per source message."""
    store = ProposalStore(engine)
    source_kind = payload["source_kind"]
    message_id = int(payload["message_id"])
    if store.by_source(tenant.id, source_kind, str(message_id)):
        return
    with engine.connect() as conn:
        msg = conn.execute(sa.select(messages).where(messages.c.id == message_id)).mappings().first()
        if msg is None:
            raise RuntimeError(f"draft for unknown message {message_id}")
        thread = conn.execute(sa.select(messages).where(
            messages.c.tenant_id == tenant.id, messages.c.from_number == msg["from_number"],
            messages.c.channel == "sms").order_by(messages.c.at)).mappings().all()

    if source_kind == "sms":
        inbound = [Msg(str(r["id"]), r["from_number"], r["body"], aware(r["at"]).timestamp())
                   for r in thread if r["direction"] == "in"]
        outbound = [Msg(str(r["id"]), "us", r["body"], aware(r["at"]).timestamp())
                    for r in thread if r["direction"] == "out"]
        newest = latest_unanswered(inbound, outbound)
        if newest is None or newest.id != str(message_id):
            return                                   # answered, or superseded by a newer text

    contact = adapters.contacts_for(tenant, engine).lookup(tenant.id, msg["from_number"]) or {}
    now = utcnow()
    deadline, hint = find_deadline(msg["body"], now, tenant.timezone)
    if deadline is None:
        deadline = now + timedelta(days=tenant.owner.default_deadline_days)
    prior = tuple(f"{'in' if r['direction'] == 'in' else 'out'}: {r['body']}"
                  for r in thread if r["id"] != message_id)[-6:]
    req = DraftRequest(
        business_name=tenant.display_name, owner_name=tenant.owner.name,
        counterparty_number=msg["from_number"], counterparty_name=contact.get("name"),
        channel="call" if source_kind == "call_message" else "sms",
        their_message=msg["body"], prior=prior, deadline_hint=hint)
    draft = adapters.llm_for(tenant).draft_reply(req)
    p = store.propose(tenant.id, msg["from_number"], draft.body, to_name=contact.get("name"),
                      basis=draft.basis, source_kind=source_kind, source_id=str(message_id),
                      deadline=deadline)
    if source_kind == "sms":
        # the newest text is the one to answer; older pending drafts to this number are moot
        for older in store.pending(tenant.id):
            if older.id != p.id and older.to_number == p.to_number and older.source_kind == "sms":
                store.supersede(older.id)
    with engine.begin() as conn:
        append_event(conn, tenant.id, "proposal.created",
                     {"proposal_id": p.id, "source": source_kind}, call_id=msg["call_id"])
        if tenant.owner.digest_at == "immediate":
            enqueue_once(conn, tenant.id, "digest")


def _digest(engine, registry: TenantRegistry, tenant: Tenant, payload: dict) -> None:
    make_rulings(engine).send_digest(tenant)


def _dispatch(engine, registry: TenantRegistry, tenant: Tenant, payload: dict) -> None:
    """A signed proposal goes out as the owner. The status transitions are the idempotency:
    only SIGNED becomes SENDING, and a job retry after a failure puts it back to SIGNED first."""
    store = ProposalStore(engine)
    pid = int(payload["proposal_id"])
    p = store.get(pid)
    if p is None:
        raise RuntimeError(f"dispatch for unknown proposal {pid}")
    if p.status == FAILED:
        p = store.retry(pid) or p
    if p.status != SIGNED:
        return                                       # already sent, or never signed: nothing to do
    if store.begin_send(pid) is None:
        return
    try:
        ref = adapters.messaging_for(tenant).send_sms(p.to_number, p.body)
    except Exception as exc:                         # noqa: BLE001
        store.mark_failed(pid, repr(exc))
        jsonl.append("rulings", {"tenant": tenant.id, "proposal": pid, "verdict": "send_failed", "error": repr(exc)})
        raise
    with engine.begin() as conn:
        record_action(conn, tenant.id, f"proposal:{pid}:send", "send_sms",
                      {"to": p.to_number, "proposal_id": pid, "edited": p.edited, "ref": ref})
        conn.execute(messages.insert().values(
            tenant_id=tenant.id, call_id=None, from_number=p.to_number, body=p.body, at=utcnow(),
            channel="sms", direction="out", vendor_ref=ref))
        append_event(conn, tenant.id, "proposal.sent", {"proposal_id": pid, "edited": p.edited})
    store.mark_sent(pid, ref)
    jsonl.append("rulings", {"tenant": tenant.id, "proposal": pid, "verdict": "sent",
                             "edited": p.edited, "to": p.to_number, "body": p.body})


def _expire(engine, registry: TenantRegistry, tenant: Tenant, payload: dict) -> None:
    make_rulings(engine).expire_pass(tenant)


def _heartbeat(engine, registry: TenantRegistry, tenant: Tenant, payload: dict) -> None:
    """Probe the tenant's adapters; page the OPERATOR (env HTR_OPERATOR_PHONE) on failure.
    The client never sees this."""
    problems: list[str] = []
    try:
        adapters.calendar_for(tenant, engine).available_slots(datetime.now(timezone.utc))
    except Exception as exc:                                     # noqa: BLE001
        problems.append(f"calendar: {exc!r}")
    try:
        adapters.contacts_for(tenant, engine).lookup(tenant.id, "+10000000000")
    except Exception as exc:                                     # noqa: BLE001
        problems.append(f"contacts: {exc!r}")
    with engine.begin() as conn:
        append_event(conn, tenant.id, "heartbeat", {"ok": not problems, "problems": problems})
    if problems:
        operator = os.environ.get("HTR_OPERATOR_PHONE")
        if operator:
            adapters.messaging_for(tenant).send_sms(
                operator, f"[htr] {tenant.id} heartbeat FAILED: " + "; ".join(problems))
        raise RuntimeError("; ".join(problems))


HANDLERS = {
    "post_call": _post_call, "draft": _draft, "digest": _digest,
    "dispatch": _dispatch, "expire": _expire, "heartbeat": _heartbeat,
}


# ---------------------------------------------------------------- worker step

def run_pending(engine, registry: TenantRegistry, limit: int = 20) -> int:
    """One drain pass. Returns how many jobs it ran. The worker loop calls this forever;
    tests call it once (or until it returns 0)."""
    ran = 0
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(jobs).where(jobs.c.status == "pending", jobs.c.run_after <= utcnow())
            .order_by(jobs.c.id).limit(limit)).mappings().all()
    for row in rows:
        tenant = registry.get(row["tenant_id"])
        handler = HANDLERS.get(row["kind"])
        try:
            if handler is None:
                raise RuntimeError(f"unknown job kind {row['kind']!r}")
            handler(engine, registry, tenant, row["payload"])
        except Exception as exc:                                 # noqa: BLE001
            attempts = row["attempts"] + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
            log.warning("job.retry" if status == "pending" else "job.failed",
                        extra={"job": row["id"], "kind": row["kind"], "error": repr(exc)})
            with engine.begin() as conn:
                conn.execute(jobs.update().where(jobs.c.id == row["id"]).values(
                    attempts=attempts, status=status, error=repr(exc),
                    run_after=utcnow() + timedelta(seconds=min(300, 10 * attempts)),
                    finished_at=utcnow() if status == "failed" else None))
        else:
            with engine.begin() as conn:
                conn.execute(jobs.update().where(jobs.c.id == row["id"]).values(
                    status="done", attempts=row["attempts"] + 1, finished_at=utcnow()))
            ran += 1
    return ran


def drain(engine, registry: TenantRegistry, max_passes: int = 10) -> int:
    """Run passes until the queue is quiet. Tests use it; the worker uses run_pending."""
    total = 0
    for _ in range(max_passes):
        n = run_pending(engine, registry)
        total += n
        if n == 0:
            break
    return total
