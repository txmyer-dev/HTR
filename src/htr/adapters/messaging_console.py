"""Dev messaging (STEALS: switchboard): SMS captured in memory and logged. Same port as Twilio,
so tests and client zero run without spending a cent. `sent` is class-level on purpose: the
registry builds adapters per call, and tests need one place to read what went out."""
from __future__ import annotations

import logging

log = logging.getLogger("htr.sms")


class ConsoleMessaging:
    can_text_callers = True
    sent: list[tuple[str, str]] = []          # shared across instances, by design

    def send_sms(self, to: str, body: str) -> str:
        ConsoleMessaging.sent.append((to, body))
        log.info("sms.send", extra={"to": to, "body": body})
        return f"console-{len(ConsoleMessaging.sent)}"

    def verify_inbound(self, url: str, params: dict[str, str], signature: str) -> bool:
        return True

    @classmethod
    def clear(cls) -> None:
        cls.sent.clear()
