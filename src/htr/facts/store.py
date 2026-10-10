"""The second brain: what the owner has told the room (STEALS: htr-convex convex/facts.ts).

A draft that could not answer a question names the gap (proposals.gap); the owner's reply to
it is the answer, kept here in the owner's own words. A reply is a letter, though, with a
greeting and a name in it, and a model handed a letter copies the letter; so each fact is also
distilled, once, into one or two plain statements about the business — what the drafter reads
from then on. The owner's words stay as the record. Distillation is a separate job
(`distill_fact`, jobs.py): this module only stores.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import sqlalchemy as sa

from ..db import aware, facts, utcnow

DISTILL = (
    "You turn a business owner's reply into what it tells you about the business. "
    "Return JSON: {\"statement\": string}: one or two plain sentences, third person, present tense, "
    "facts only (hours, prices, policies, places, how to book). No greeting, no names of people, "
    "no sign-off, no pleasantries. If the reply states no fact about the business, "
    "return {\"statement\": null}."
)


@dataclass(frozen=True)
class Fact:
    id: int
    tenant_id: str
    question: str | None
    answer: str
    proposal_id: int | None
    statement: str | None
    distilled_at: datetime | None
    at: datetime

    @classmethod
    def from_row(cls, r: Any) -> "Fact":
        return cls(id=r["id"], tenant_id=r["tenant_id"], question=r["question"], answer=r["answer"],
                   proposal_id=r["proposal_id"], statement=r["statement"],
                   distilled_at=aware(r["distilled_at"]), at=aware(r["at"]))


def fact_text(f: Fact) -> str:
    """What the drafter reads for a fact: the distilled statement when there is one, else the
    owner's words."""
    return f.statement or f.answer


class FactStore:
    def __init__(self, engine: sa.Engine, now: Callable[[], datetime] | None = None):
        self.engine = engine
        self.now = now or utcnow

    def get(self, fact_id: int) -> Fact | None:
        with self.engine.connect() as conn:
            r = conn.execute(sa.select(facts).where(facts.c.id == fact_id)).mappings().first()
        return Fact.from_row(r) if r else None

    def recent(self, tenant_id: str, limit: int = 50) -> list[Fact]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(facts).where(facts.c.tenant_id == tenant_id)
                                .order_by(facts.c.at.desc()).limit(limit)).mappings().all()
        return [Fact.from_row(r) for r in rows]

    def learn(self, tenant_id: str, answer: str, question: str | None = None,
             proposal_id: int | None = None) -> Fact | None:
        """Keep a fact in the owner's words. Once per (question, answer): a replayed ruling
        teaches nothing new. Returns the new fact, or None when there was nothing to learn or it
        was already known."""
        text = " ".join(answer.split())
        if not text:
            return None
        if any(f.answer == text and f.question == question for f in self.recent(tenant_id, 50)):
            return None
        with self.engine.begin() as conn:
            res = conn.execute(facts.insert().values(
                tenant_id=tenant_id, question=question, answer=text, proposal_id=proposal_id, at=self.now()))
            fid = int(res.inserted_primary_key[0])
        return self.get(fid)

    def set_statement(self, fact_id: int, statement: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(facts.update().where(facts.c.id == fact_id).values(
                statement=statement, distilled_at=self.now()))
