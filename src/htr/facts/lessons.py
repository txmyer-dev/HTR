"""The owner's corrections, as lessons for the drafter (STEALS: htr-convex
lib/drafter/lessons.ts).

An edit ruling is the owner saying "not that, this": the draft the machine wrote and the words
that actually went out. Nothing is asked of the owner after that. The next draft for this
tenant sees the last few pairs and is told to match the owner's words, not the earlier drafts.
Edits only (a skip says "not this" without saying what instead, and is not a lesson); per
tenant only.
"""
from __future__ import annotations

import sqlalchemy as sa

from ..db import proposals, rulings
from ..ports.llm import Lesson

MAX_LESSONS = 5
MAX_CHARS = 500


def _squash(s: str | None) -> str:
    return " ".join((s or "").split())


def _clip(s: str) -> str:
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS - 1].rstrip() + "…"


def to_lessons(rows: list[dict], max_: int = MAX_LESSONS) -> list[Lesson]:
    """Rows newest first, already filtered to edit rulings by the caller. Drops pairs with
    nothing to learn from (an empty side, or words that only differ by whitespace), clips each
    side, and keeps at most `max_`, newest first."""
    out: list[Lesson] = []
    for r in rows:
        draft = _squash(r.get("draft"))
        sent = _squash(r.get("sent"))
        if not draft or not sent or draft == sent:
            continue
        out.append(Lesson(their_message=_clip(_squash(r.get("their_message"))), draft=_clip(draft), sent=_clip(sent)))
        if len(out) >= max_:
            break
    return out


def recent_lessons(engine: sa.Engine, tenant_id: str, limit: int = MAX_LESSONS) -> list[Lesson]:
    """The tenant's last few edit rulings, joined back to the proposal for what the counterparty
    said (the basis the superseded draft rested on)."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(rulings.c.draft_body, rulings.c.edit_body, proposals.c.basis)
            .join(proposals, proposals.c.id == rulings.c.proposal_id)
            .where(rulings.c.tenant_id == tenant_id, rulings.c.verdict == "edit", rulings.c.draft_body.isnot(None))
            .order_by(rulings.c.at.desc()).limit(limit * 2)  # a few extra: some pairs get dropped as no-lessons
        ).mappings().all()
    candidates = [{"their_message": (r["basis"] or [""])[0], "draft": r["draft_body"], "sent": r["edit_body"]}
                 for r in rows]
    return to_lessons(candidates, limit)
