"""Expiry: the state none of the source repos had. A draft with a deadline stops waiting when
the deadline passes with no ruling. The assistant then does the one thing it may do alone:
tells the counterparty a reply is coming and when, in the owner's hold-the-room voice, and
re-lists the item at the top of the next digest. It never sends the draft."""
from __future__ import annotations

from datetime import datetime

from ..config import Tenant
from ..proposals.store import PENDING, Proposal, ProposalStore


def overdue(store: ProposalStore, tenant_id: str, now: datetime) -> list[Proposal]:
    return [p for p in store.with_status(tenant_id, PENDING) if p.deadline and p.deadline <= now]


def hold_notice(tenant: Tenant) -> str:
    who = tenant.owner.name
    tail = " (This is an automated note from their assistant.)" if tenant.owner.disclose_assistant else ""
    return (f"Hi, this is {tenant.display_name}. {who} has seen your message and will reply "
            f"personally as soon as possible.{tail}")
