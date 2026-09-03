"""The drafting port. The only thing the loop needs from a model: given what someone said and who
they are, one reply in the owner's voice plus the quotes it rests on (the Basis discipline,
STEALS: icm-agent). Which model, and where it runs, is an adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DraftRequest:
    business_name: str
    owner_name: str
    counterparty_number: str
    counterparty_name: str | None
    channel: str                       # call | sms
    their_message: str
    prior: tuple[str, ...] = ()        # earlier lines in the thread, oldest first, "in: ..." / "out: ..."
    deadline_hint: str | None = None   # "Friday", "by Thursday" ... if the reader found one


@dataclass(frozen=True)
class Draft:
    body: str
    basis: tuple[str, ...] = field(default_factory=tuple)   # verbatim quotes from their_message / prior


class LLMPort(Protocol):
    def draft_reply(self, req: DraftRequest) -> Draft: ...
