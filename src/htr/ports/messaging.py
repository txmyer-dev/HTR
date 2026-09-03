from __future__ import annotations

from typing import Protocol


class MessagingPort(Protocol):
    def send_sms(self, to: str, body: str) -> None:
        """Post-call path only, except send_link's single outbound text."""
        ...
