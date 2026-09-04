"""Gmail over the REST API with an OAuth refresh token. No Google SDK: three endpoints do not
justify one. Scopes: gmail.readonly (intake) and gmail.send (dispatch as the owner).

Env: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN (from
`python -m htr.gmail_auth`), GMAIL_ADDRESS (the owner's address, used as From).
"""
from __future__ import annotations

import base64
import os
import re
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from ..ports.mail import InboundMail, OutboundMail

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
AUTOMATED_HINTS = ("noreply", "no-reply", "donotreply", "mailer-daemon", "notifications@", "newsletter")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _text_of(payload: dict) -> str:
    """The text/plain part, or a crude strip of text/html, walking multipart bodies."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _unb64url(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        t = _text_of(part)
        if t:
            return t
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = _unb64url(payload["body"]["data"]).decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", "", html))
    return ""


class GmailMail:
    def __init__(self, secrets: dict[str, str | None] | None = None):
        s = {k: v for k, v in (secrets or {}).items() if v}
        g = os.environ.get
        self.client_id = s.get("google_oauth_client_id") or g("GOOGLE_OAUTH_CLIENT_ID", "")
        self.client_secret = s.get("google_oauth_client_secret") or g("GOOGLE_OAUTH_CLIENT_SECRET", "")
        self.refresh_token = s.get("google_refresh_token") or g("GOOGLE_REFRESH_TOKEN", "")
        self.address = s.get("gmail_address") or g("GMAIL_ADDRESS", "")
        self._token: str = ""
        self._token_exp: float = 0.0

    # ---- auth ----------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise RuntimeError("Gmail not configured (GOOGLE_OAUTH_CLIENT_ID/SECRET, GOOGLE_REFRESH_TOKEN)")
        r = httpx.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                        "refresh_token": self.refresh_token, "grant_type": "refresh_token"}, timeout=20)
        r.raise_for_status()
        j = r.json()
        self._token, self._token_exp = j["access_token"], time.time() + int(j.get("expires_in", 3600))
        return self._token

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    # ---- intake ----------------------------------------------------------------

    def pull(self, since: datetime | None = None, limit: int = 50) -> list[InboundMail]:
        q = "in:inbox -from:me -category:promotions -category:social"
        if since:
            q += f" after:{int(since.timestamp())}"
        r = httpx.get(f"{API}/messages", headers=self._h(), params={"q": q, "maxResults": limit}, timeout=20)
        r.raise_for_status()
        out: list[InboundMail] = []
        for ref in r.json().get("messages", []) or []:
            m = httpx.get(f"{API}/messages/{ref['id']}", headers=self._h(), params={"format": "full"}, timeout=20)
            m.raise_for_status()
            j = m.json()
            hdr = {h["name"].lower(): h["value"] for h in j.get("payload", {}).get("headers", [])}
            name, addr = parseaddr(hdr.get("from", ""))
            automated = (any(x in addr.lower() for x in AUTOMATED_HINTS) or "list-unsubscribe" in hdr
                         or hdr.get("precedence", "").lower() in ("bulk", "list", "junk")
                         or hdr.get("auto-submitted", "no").lower() != "no")
            out.append(InboundMail(
                id=j["id"], thread_id=j.get("threadId", ""), from_address=addr.lower(), from_name=name or None,
                subject=hdr.get("subject", ""), text=_text_of(j.get("payload", {})).strip(),
                received_at=datetime.fromtimestamp(int(j.get("internalDate", "0")) / 1000, tz=timezone.utc),
                message_id=hdr.get("message-id"), references=hdr.get("references"), automated=automated))
        return out

    # ---- dispatch ----------------------------------------------------------------

    def send(self, mail: OutboundMail) -> str:
        if not self.address:
            raise RuntimeError("GMAIL_ADDRESS not configured")
        msg = EmailMessage()
        msg["To"] = mail.to_address
        msg["From"] = self.address
        msg["Subject"] = mail.subject
        if mail.in_reply_to:
            msg["In-Reply-To"] = mail.in_reply_to
            msg["References"] = (mail.references + " " if mail.references else "") + mail.in_reply_to
        msg.set_content(mail.text)
        body = {"raw": _b64url(msg.as_bytes())}
        if mail.thread_id:
            body["threadId"] = mail.thread_id
        r = httpx.post(f"{API}/messages/send", headers=self._h(), json=body, timeout=20)
        r.raise_for_status()
        return str(r.json().get("id", ""))
