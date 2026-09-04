"""Dev mail: an inbox you seed and an outbox you read. Same port as Gmail, so the email loop
runs in tests and on a laptop with no account."""
from __future__ import annotations

from datetime import datetime

from ..ports.mail import InboundMail, OutboundMail


class ConsoleMail:
    inbox: list[InboundMail] = []                 # class-level on purpose, like ConsoleMessaging
    sent: list[OutboundMail] = []

    def pull(self, since: datetime | None = None, limit: int = 50) -> list[InboundMail]:
        items = [m for m in ConsoleMail.inbox if since is None or m.received_at > since]
        return items[:limit]

    def send(self, mail: OutboundMail) -> str:
        ConsoleMail.sent.append(mail)
        return f"console-mail-{len(ConsoleMail.sent)}"

    @classmethod
    def clear(cls) -> None:
        cls.inbox.clear()
        cls.sent.clear()
