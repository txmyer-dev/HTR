"""The proposal store (STEALS: waggle src/proposals/store.ts, ported to a table).

A proposal is a draft with a status. Approving one is the only path from "the assistant said
so" to "a message went out as the owner", and it runs through the owner's ruling exactly once.

Lifecycle: pending -> signed -> sending -> sent
           pending -> rejected | expired
           sending -> failed -> signed (retry)

Ids never restart (autoincrement), so a ruling keyed by proposal id can never land on a
different proposal than the one it was made about.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import sqlalchemy as sa

from ..db import aware, proposals, utcnow

PENDING, SIGNED, SENDING, SENT, REJECTED, EXPIRED, FAILED, SUPERSEDED = (
    "pending", "signed", "sending", "sent", "rejected", "expired", "failed", "superseded")


def _truncate(s: str, n: int) -> str:
    one = " ".join(s.split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def summarize(to_name: str | None, to_number: str, body: str) -> str:
    who = to_name or to_number
    return f"Reply to {who}: “{_truncate(body, 60)}”"


@dataclass(frozen=True)
class Proposal:
    id: int
    tenant_id: str
    kind: str
    to_number: str
    to_name: str | None
    body: str
    basis: tuple[str, ...]
    summary: str
    status: str
    source_kind: str | None
    source_id: str | None
    deadline: datetime | None
    digest_no: int | None
    digest_id: int | None
    edited: bool
    sent_ref: str | None
    error: str | None
    meta: dict
    gap: str | None
    created_at: datetime
    ruled_at: datetime | None
    sent_at: datetime | None

    @classmethod
    def from_row(cls, r: Any) -> "Proposal":
        return cls(
            id=r["id"], tenant_id=r["tenant_id"], kind=r["kind"], to_number=r["to_number"],
            to_name=r["to_name"], body=r["body"], basis=tuple(r["basis"] or ()), summary=r["summary"],
            status=r["status"], source_kind=r["source_kind"], source_id=r["source_id"],
            deadline=aware(r["deadline"]), digest_no=r["digest_no"], digest_id=r["digest_id"],
            edited=bool(r["edited"]), sent_ref=r["sent_ref"], error=r["error"], meta=dict(r["meta"] or {}),
            gap=r["gap"],
            created_at=aware(r["created_at"]), ruled_at=aware(r["ruled_at"]), sent_at=aware(r["sent_at"]))


class ProposalStore:
    def __init__(self, engine: sa.Engine, now: Callable[[], datetime] | None = None):
        self.engine = engine
        self.now = now or utcnow

    # ---- reads -------------------------------------------------------------

    def get(self, proposal_id: int) -> Proposal | None:
        with self.engine.connect() as conn:
            r = conn.execute(sa.select(proposals).where(proposals.c.id == proposal_id)).mappings().first()
        return Proposal.from_row(r) if r else None

    def by_source(self, tenant_id: str, source_kind: str, source_id: str) -> Proposal | None:
        with self.engine.connect() as conn:
            r = conn.execute(sa.select(proposals).where(
                proposals.c.tenant_id == tenant_id, proposals.c.source_kind == source_kind,
                proposals.c.source_id == source_id)).mappings().first()
        return Proposal.from_row(r) if r else None

    def with_status(self, tenant_id: str, *statuses: str) -> list[Proposal]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(proposals).where(
                proposals.c.tenant_id == tenant_id, proposals.c.status.in_(statuses))
                .order_by(proposals.c.created_at, proposals.c.id)).mappings().all()
        return [Proposal.from_row(r) for r in rows]

    def pending(self, tenant_id: str) -> list[Proposal]:
        return self.with_status(tenant_id, PENDING)

    def pending_count(self, tenant_id: str) -> int:
        return len(self.pending(tenant_id))

    # ---- writes ------------------------------------------------------------

    def propose(self, tenant_id: str, to_number: str, body: str, *, to_name: str | None = None,
                basis: tuple[str, ...] | list[str] = (), source_kind: str | None = None,
                source_id: str | None = None, deadline: datetime | None = None,
                kind: str = "sms", meta: dict | None = None, gap: str | None = None) -> Proposal:
        with self.engine.begin() as conn:
            res = conn.execute(proposals.insert().values(
                tenant_id=tenant_id, kind=kind, to_number=to_number, to_name=to_name, body=body,
                basis=list(basis), summary=summarize(to_name, to_number, body), status=PENDING,
                source_kind=source_kind, source_id=source_id, deadline=deadline, gap=gap,
                edited=False, meta=meta or {}, created_at=self.now()))
            pid = int(res.inserted_primary_key[0])
        return self.get(pid)  # type: ignore[return-value]

    def _transition(self, proposal_id: int, from_statuses: tuple[str, ...], **values: Any) -> Proposal | None:
        """Move a proposal along the lifecycle; a no-op unless it is in one of from_statuses.
        The WHERE on status is what makes a replayed ruling or a double dispatch harmless."""
        with self.engine.begin() as conn:
            res = conn.execute(proposals.update().where(
                proposals.c.id == proposal_id, proposals.c.status.in_(from_statuses)).values(**values))
            if res.rowcount != 1:
                return None
        return self.get(proposal_id)

    def edit(self, proposal_id: int, body: str) -> Proposal | None:
        """The owner replaced the words. Only pending proposals are editable."""
        p = self.get(proposal_id)
        if not p or p.status != PENDING:
            return None
        return self._transition(proposal_id, (PENDING,), body=body, edited=True,
                                summary=summarize(p.to_name, p.to_number, body))

    def reject(self, proposal_id: int) -> Proposal | None:
        return self._transition(proposal_id, (PENDING,), status=REJECTED, ruled_at=self.now())

    def approve(self, proposal_id: int) -> Proposal | None:
        """The owner ruled: sign it. Sending is the dispatcher's job; this only records that the
        ruling happened, once. Approving a proposal twice returns None the second time."""
        return self._transition(proposal_id, (PENDING,), status=SIGNED, ruled_at=self.now())

    def expire(self, proposal_id: int) -> Proposal | None:
        return self._transition(proposal_id, (PENDING,), status=EXPIRED, ruled_at=self.now())

    def supersede(self, proposal_id: int) -> Proposal | None:
        """A newer message from the same counterparty made this draft moot before anyone ruled."""
        return self._transition(proposal_id, (PENDING,), status=SUPERSEDED, ruled_at=self.now())

    def begin_send(self, proposal_id: int) -> Proposal | None:
        return self._transition(proposal_id, (SIGNED,), status=SENDING)

    def mark_sent(self, proposal_id: int, ref: str | None) -> Proposal | None:
        return self._transition(proposal_id, (SENDING,), status=SENT, sent_ref=ref, sent_at=self.now(), error=None)

    def mark_failed(self, proposal_id: int, error: str) -> Proposal | None:
        return self._transition(proposal_id, (SENDING,), status=FAILED, error=error)

    def retry(self, proposal_id: int) -> Proposal | None:
        """Put a failed one back on the table: it is still signed, it just did not go out."""
        return self._transition(proposal_id, (FAILED,), status=SIGNED, error=None)

    def assign_digest(self, digest_id: int, numbering: dict[int, int]) -> None:
        """numbering: proposal_id -> digest number."""
        with self.engine.begin() as conn:
            for pid, n in numbering.items():
                conn.execute(proposals.update().where(proposals.c.id == pid).values(
                    digest_id=digest_id, digest_no=n))
