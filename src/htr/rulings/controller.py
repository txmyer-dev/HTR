"""Rule by text (STEALS: waggle src/rulings/controller.ts, ported; the transport is now the
numbered SMS digest out and the inbound SMS webhook back).

Every pending proposal is numbered into a digest sent to the owner's phone. A reply from that
number is a ruling; a reply from any other number is ignored and logged. This module applies
the ruling once, enqueues the dispatch, and answers the owner with what it did.

Two invariants carried over from Waggle:
- Only the owner's number may rule. Checked here, not in the webhook.
- A number in a reply resolves only through the *latest* digest's mapping, and only to a
  proposal that is still pending. A stale digest cannot approve a newer proposal.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Callable

import sqlalchemy as sa

from ..config import Tenant
from ..db import digests, rulings as rulings_t, utcnow
from ..digest.expiry import hold_notice, overdue
from ..digest.render import render_digest
from ..events import append_event, record_action
from ..log import jsonl
from ..proposals.store import PENDING, Proposal, ProposalStore
from .parse import Verdict, parse_reply

log = logging.getLogger("htr.rulings")

def ruling_token(secret: str, tenant_id: str, proposal_id: int, verdict: str) -> str:
    """The signature on a ruling button. A leaked notification cannot rule on anything else,
    and cannot rule the other way."""
    msg = f"{tenant_id}:{proposal_id}:{verdict}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def ruling_secret(tenant: Tenant) -> str | None:
    return tenant.secrets.get("ruling_secret") or os.environ.get("HTR_RULING_SECRET") or None


HELP = ("I read replies like: \"1\" or \"1 yes\" to send draft 1, \"2 no\" to skip it, "
        "\"3 your own words\" to send those instead, \"all\", \"later\", \"hold\", or \"?\" for the list.")


class Rulings:
    def __init__(self, engine: sa.Engine, store: ProposalStore, messaging_for: Callable,
                 enqueue_dispatch: Callable[[str, int], None], now: Callable[[], datetime] | None = None,
                 counterparty_messaging_for: Callable | None = None):
        self.engine = engine
        self.store = store
        self.messaging_for = messaging_for                       # the owner's channel
        self.counterparty_messaging_for = counterparty_messaging_for or messaging_for
        self.enqueue_dispatch = enqueue_dispatch
        self.now = now or utcnow

    # ---- outbound: the digest ------------------------------------------------

    def latest_digest(self, tenant_id: str):
        with self.engine.connect() as conn:
            return conn.execute(sa.select(digests).where(digests.c.tenant_id == tenant_id)
                                .order_by(digests.c.id.desc()).limit(1)).mappings().first()

    def send_digest(self, tenant: Tenant) -> int | None:
        """Number every pending proposal, send one SMS, remember the numbering."""
        pending = self.store.pending(tenant.id)
        if not pending:
            return None
        numbered = [(n, p) for n, p in enumerate(pending, start=1)]
        body = render_digest(numbered, timezone=tenant.timezone, now=self.now())
        mapping = {str(n): p.id for n, p in numbered}
        with self.engine.begin() as conn:
            res = conn.execute(digests.insert().values(
                tenant_id=tenant.id, mapping=mapping, body=body, sent_at=self.now()))
            digest_id = int(res.inserted_primary_key[0])
            append_event(conn, tenant.id, "digest.sent", {"digest_id": digest_id, "count": len(numbered)})
        self.store.assign_digest(digest_id, {p.id: n for n, p in numbered})
        messenger = self.messaging_for(tenant)
        messenger.send_sms(tenant.owner.phone, body)
        self._send_buttons(tenant, messenger, numbered)
        return digest_id

    def _send_buttons(self, tenant: Tenant, messenger, numbered: list[tuple[int, Proposal]]) -> None:
        """One notification per draft with Send / Skip buttons, when the channel can carry them
        and a public ruling URL plus a secret exist. Edits still need words from another channel."""
        send = getattr(messenger, "send_with_actions", None)
        base = os.environ.get("HTR_RULING_URL", "").rstrip("/")
        secret = ruling_secret(tenant)
        if send is None or not base or not secret:
            return
        for n, p in numbered:
            who = p.to_name or p.to_number
            actions = [(label, f"{base}/{tenant.id}/{p.id}/{verdict}?t={ruling_token(secret, tenant.id, p.id, verdict)}")
                       for label, verdict in (("Send", "sign"), ("Skip", "reject"))]
            try:
                send(f"{n} - {who}", p.body, actions)
            except Exception as exc:                                    # noqa: BLE001
                log.warning("digest.buttons_failed", extra={"proposal": p.id, "error": repr(exc)})

    def rule_by_id(self, tenant: Tenant, proposal_id: int, verdict: Verdict, via: str = "button") -> str:
        """A ruling that names the proposal directly (a button), not a digest number."""
        p = self.store.get(proposal_id)
        if p is None or p.tenant_id != tenant.id or p.status != PENDING:
            return "Nothing waiting under that draft."
        note = self._apply(tenant, p, p.digest_no or p.id, verdict, p.digest_id, via, f"{via}:{verdict.kind}")
        with self.engine.begin() as conn:
            append_event(conn, tenant.id, "ruling.applied", {"rulings": 1, "via": via})
        self.messaging_for(tenant).send_sms(tenant.owner.phone, note)
        return note

    # ---- inbound: rulings ----------------------------------------------------

    def handle_owner_reply(self, tenant: Tenant, from_number: str, text: str) -> str | None:
        """Apply what the owner said. Returns the confirmation that was texted back, or None
        when the sender was not the owner (the caller treats it as an ordinary inbound)."""
        if from_number != tenant.owner.phone:
            self._record(tenant.id, None, None, from_number, text, "ignored")
            log.warning("ruling.ignored", extra={"tenant": tenant.id, "from": from_number})
            return None

        parsed = parse_reply(text)
        digest = self.latest_digest(tenant.id)
        mapping: dict[str, int] = dict(digest["mapping"]) if digest else {}
        digest_id = int(digest["id"]) if digest else None
        notes: list[str] = []

        for n, verdict in parsed.rulings:
            pid = mapping.get(str(n))
            p = self.store.get(pid) if pid else None
            if not p or p.status != PENDING:
                notes.append(f"{n}: nothing waiting under that number.")
                self._record(tenant.id, pid, digest_id, from_number, text, "ignored")
                continue
            notes.append(self._apply(tenant, p, n, verdict, digest_id, from_number, text))

        for cmd, arg in parsed.commands:
            if cmd == "all":
                targets = [p for p in self.store.pending(tenant.id) if p.digest_id == digest_id] if digest_id else []
                if not targets:
                    notes.append("Nothing in the last digest is still waiting.")
                for p in targets:
                    notes.append(self._apply(tenant, p, p.digest_no or 0, Verdict("sign"), digest_id, from_number, text))
            elif cmd == "later":
                n_pending = self.store.pending_count(tenant.id)
                notes.append(f"Left {n_pending} waiting for the next digest.")
                self._record(tenant.id, None, digest_id, from_number, text, "command:later")
            elif cmd == "hold":
                self.set_hold(tenant, arg)
                notes.append(f"Holding the room{' until ' + arg if arg else ''}. Drafts keep stacking; reply ? any time.")
            elif cmd == "digest":
                self._record(tenant.id, None, digest_id, from_number, text, "command:digest")
                if self.send_digest(tenant) is not None:
                    return None                  # the digest itself is the reply
                notes.append("Nothing waiting on you.")

        if parsed.empty and parsed.unparsed:
            notes.append(HELP)
        if not notes:
            return None
        confirmation = "\n".join(notes)
        with self.engine.begin() as conn:
            append_event(conn, tenant.id, "ruling.applied", {"rulings": len(parsed.rulings),
                                                             "commands": [c for c, _ in parsed.commands]})
        self.messaging_for(tenant).send_sms(tenant.owner.phone, confirmation)
        return confirmation

    def _apply(self, tenant: Tenant, p: Proposal, n: int, verdict: Verdict, digest_id: int | None,
               from_number: str, raw: str) -> str:
        who = p.to_name or p.to_number
        if verdict.kind == "reject":
            self.store.reject(p.id)
            self._record(tenant.id, p.id, digest_id, from_number, raw, "reject")
            return f"Skipped {n} ({who})."
        edited = False
        if verdict.kind == "edit" and verdict.body:
            self.store.edit(p.id, verdict.body)
            edited = True
        signed = self.store.approve(p.id)
        if signed is None:                       # raced: already ruled
            return f"{n}: already ruled."
        self._record(tenant.id, p.id, digest_id, from_number, raw, "edit" if edited else "sign",
                     verdict.body if edited else None)
        self.enqueue_dispatch(tenant.id, p.id)
        return f"Sending {n} to {who}{' with your words' if edited else ''}."

    def _record(self, tenant_id: str, proposal_id: int | None, digest_id: int | None,
                from_number: str, raw: str, verdict: str, edit_body: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(rulings_t.insert().values(
                tenant_id=tenant_id, proposal_id=proposal_id, digest_id=digest_id, from_number=from_number,
                raw=raw, verdict=verdict, edit_body=edit_body, at=self.now()))
        jsonl.append("rulings", {"tenant": tenant_id, "proposal": proposal_id, "digest": digest_id,
                                 "verdict": verdict, "raw": raw, "edit": edit_body})

    # ---- hold the room ---------------------------------------------------------

    def set_hold(self, tenant: Tenant, until: str | None) -> None:
        with self.engine.begin() as conn:
            append_event(conn, tenant.id, "hold.set", {"until": until})
        self._record(tenant.id, None, None, tenant.owner.phone, f"hold {until or ''}".strip(), "command:hold")

    def clear_hold(self, tenant: Tenant) -> None:
        with self.engine.begin() as conn:
            append_event(conn, tenant.id, "hold.cleared", {})

    # ---- expiry ------------------------------------------------------------------

    def expire_pass(self, tenant: Tenant) -> list[int]:
        """Overdue drafts stop waiting: tell the counterparty (the one thing the assistant may do
        alone), and re-list the item so it heads the next digest."""
        expired: list[int] = []
        for p in overdue(self.store, tenant.id, self.now()):
            if self.store.expire(p.id) is None:
                continue
            with self.engine.begin() as conn:
                first = record_action(conn, tenant.id, f"proposal:{p.id}:expired_notice", "send_sms",
                                      {"to": p.to_number, "reason": "expired"})
                append_event(conn, tenant.id, "proposal.expired", {"proposal_id": p.id})
            if first:
                out = self.counterparty_messaging_for(tenant)
                if getattr(out, "can_text_callers", False):
                    out.send_sms(p.to_number, hold_notice(tenant))
            self.store.propose(tenant.id, p.to_number, p.body, to_name=p.to_name, basis=p.basis,
                               source_kind="relist", source_id=str(p.id), deadline=None, kind=p.kind)
            jsonl.append("rulings", {"tenant": tenant.id, "proposal": p.id, "verdict": "expired"})
            expired.append(p.id)
        return expired
