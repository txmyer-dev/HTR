"""The mail port. Intake reads what arrived; send goes out as the owner, in the thread it
answers. Gmail is the first adapter; AgentMail is the Convex build's."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class InboundMail:
    id: str                       # provider message id (dedupe key)
    thread_id: str
    from_address: str
    from_name: str | None
    subject: str
    text: str
    received_at: datetime
    message_id: str | None = None          # RFC 5322 Message-ID, for In-Reply-To
    references: str | None = None
    automated: bool = False                # bulk / no-reply / list mail: never waits on the owner


@dataclass(frozen=True)
class OutboundMail:
    to_address: str
    subject: str
    text: str
    in_reply_to: str | None = None
    references: str | None = None
    thread_id: str | None = None


class MailPort(Protocol):
    def pull(self, since: datetime | None = None, limit: int = 50) -> list[InboundMail]: ...
    def send(self, mail: OutboundMail) -> str: ...
