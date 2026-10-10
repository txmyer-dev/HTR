"""Owner push over ntfy (STEALS: switchboard, extended with action buttons).

ntfy reaches the owner's phone with no telephony at all, and a notification can carry up to
three buttons. An `http` button fires a request in the background: enough to sign or skip a
draft with one tap. A `view` button opens a page: the draft's card, where the owner can read
the context and put their own words in. Tapping the notification itself opens `click`.
It cannot text callers, so `send_link` declines honestly.

Buttons go out as ntfy's JSON publish, not headers: a caller's name lands in the title, and
an HTTP header cannot carry "José".

Env: HTR_NTFY_URL (default https://ntfy.sh), HTR_NTFY_TOPIC, optional HTR_NTFY_TOKEN for a
protected topic, optional HTR_NTFY_ICON (an image URL: the room's face on every push)."""
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
        self.icon = os.environ.get("HTR_NTFY_ICON", "")

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _headers(self) -> dict[str, str]:
        h = {"Title": "Hold the Room", **self._auth()}
        if self.icon:
            h["Icon"] = self.icon
        return h

    def send_sms(self, to: str, body: str) -> str:
        """`to` is ignored: ntfy only reaches the owner. Everything else goes to the topic."""
        if not self.topic:
            raise RuntimeError("HTR_NTFY_TOPIC not configured")
        r = httpx.post(f"{self.base}/{self.topic}", content=body.encode("utf-8"),
                       headers=self._headers(), timeout=10)
        r.raise_for_status()
        return ""

    def send_with_actions(self, title: str, body: str, actions: list[tuple[str, str, str]],
                          click: str | None = None, priority: int | None = None) -> str:
        """`actions` are (label, url, kind): kind "http" rules in the background, "view" opens
        the url. ntfy shows at most three. `priority` is ntfy's 1-5 (3 is default)."""
        if not self.topic:
            raise RuntimeError("HTR_NTFY_TOPIC not configured")
        msg: dict = {"topic": self.topic, "title": title, "message": body,
                     "actions": [_action(label, url, kind) for label, url, kind in actions[:3]]}
        if click:
            msg["click"] = click
        if priority:
            msg["priority"] = priority
        if self.icon:
            msg["icon"] = self.icon
        r = httpx.post(self.base, json=msg, headers=self._auth(), timeout=10)
        r.raise_for_status()
        return ""


def _action(label: str, url: str, kind: str) -> dict:
    if kind == "view":
        return {"action": "view", "label": label, "url": url, "clear": True}
    return {"action": "http", "label": label, "url": url, "method": "POST", "clear": True}
