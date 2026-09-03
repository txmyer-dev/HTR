"""Owner push over ntfy (STEALS: switchboard, extended with action buttons).

ntfy reaches the owner's phone with no telephony at all, and a notification can carry up to
three buttons that fire HTTP requests. That is enough to sign or skip a draft with one tap.
It cannot text callers, so `send_link` declines honestly, and edits still need words from
another channel.

Env: HTR_NTFY_URL (default https://ntfy.sh), HTR_NTFY_TOPIC, optional HTR_NTFY_TOKEN for a
protected topic."""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("htr.ntfy")


class NtfyMessaging:
    can_text_callers = False

    def __init__(self) -> None:
        self.topic = os.environ.get("HTR_NTFY_TOPIC", "")
        self.base = os.environ.get("HTR_NTFY_URL", "https://ntfy.sh").rstrip("/")
        self.token = os.environ.get("HTR_NTFY_TOKEN", "")

    def _headers(self, title: str | None = None, actions: list[tuple[str, str]] | None = None) -> dict[str, str]:
        h: dict[str, str] = {"Title": title or "Hold the Room"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if actions:
            # ntfy action syntax: "http, <label>, <url>, method=POST, clear=true; ..."
            h["Actions"] = "; ".join(f"http, {label}, {url}, method=POST, clear=true" for label, url in actions)
        return h

    def send_sms(self, to: str, body: str) -> str:
        """`to` is ignored: ntfy only reaches the owner. Everything else goes to the topic."""
        if not self.topic:
            raise RuntimeError("HTR_NTFY_TOPIC not configured")
        r = httpx.post(f"{self.base}/{self.topic}", content=body.encode("utf-8"),
                       headers=self._headers(), timeout=10)
        r.raise_for_status()
        return ""

    def send_with_actions(self, title: str, body: str, actions: list[tuple[str, str]]) -> str:
        if not self.topic:
            raise RuntimeError("HTR_NTFY_TOPIC not configured")
        r = httpx.post(f"{self.base}/{self.topic}", content=body.encode("utf-8"),
                       headers=self._headers(title, actions[:3]), timeout=10)
        r.raise_for_status()
        return ""
