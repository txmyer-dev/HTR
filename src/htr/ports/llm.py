"""The drafting port. The only thing the loop needs from a model: given what someone said and who
they are, one reply in the owner's voice plus the quotes it rests on (the Basis discipline,
STEALS: icm-agent). Which model, and where it runs, is an adapter.

The second brain (STEALS: htr-convex convex/facts.ts, lib/drafter/lessons.ts) rides the same
port: `facts` is what the owner has taught the room, distilled; `lessons` is the owner's last
few corrections, in their own words. A model that cannot answer something neither says nor
answers with a guess: it names the gap, and the owner's next edit on that draft is the lesson
and the answer at once."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Lesson:
    """An edit ruling as a lesson: not that, this. (STEALS: htr-convex lib/drafter/lessons.ts)"""
    their_message: str    # what the counterparty said (the basis the draft it replaced rested on)
    draft: str             # what the machine proposed
    sent: str               # what the owner sent instead


@dataclass(frozen=True)
class DraftRequest:
    business_name: str
    owner_name: str
    counterparty_number: str
    counterparty_name: str | None
    channel: str                       # call | sms | email
    their_message: str
    prior: tuple[str, ...] = ()        # earlier lines in the thread, oldest first, "in: ..." / "out: ..."
    deadline_hint: str | None = None   # "Friday", "by Thursday" ... if the reader found one
    facts: tuple[str, ...] = ()        # what the owner has taught the room (fact_text per fact, newest last)
    lessons: tuple[Lesson, ...] = ()   # the owner's last few corrections, newest first


@dataclass(frozen=True)
class Draft:
    body: str
    basis: tuple[str, ...] = field(default_factory=tuple)   # verbatim quotes from their_message / prior
    gap: str | None = None             # what they asked that nothing here answers; named, not guessed


class LLMPort(Protocol):
    def draft_reply(self, req: DraftRequest) -> Draft: ...
    def distill_fact(self, question: str | None, answer: str) -> str | None:
        """The owner's reply, distilled once into a plain statement about the business, third
        person, present tense. None on a failure or a reply with no fact in it: the owner's
        words stand as what the drafter reads."""
        ...
