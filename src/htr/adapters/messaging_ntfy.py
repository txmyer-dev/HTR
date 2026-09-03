"""Push notifications via ntfy.sh — the no-SMS messaging adapter for a voice-only number.

Owner summaries and operator pages reach the subscribed phone for free. What this adapter
cannot do is reach an arbitrary caller (can_text_callers=False): send_link declines gracefully
instead of pretending. Twilio replaces this behind the same port when a texting number exists."""
from __future__ import annotations

import os

import httpx


class NtfyMessaging:
    can_text_callers = False

    def __init__(self):
        self.topic = os.environ.get("HTR_NTFY_TOPIC", "")
        self.base = os.environ.get("HTR_NTFY_URL", "https://ntfy.sh")

    def send_sms(self, to: str, body: str) -> None:
        if not self.topic:
            raise RuntimeError("HTR_NTFY_TOPIC not configured")
        r = httpx.post(f"{self.base}/{self.topic}", content=body.encode(),
                       headers={"Title": f"htr -> {to}"}, timeout=10)
        r.raise_for_status()
