"""AgentMail: a hosted inbox with a webhook, not a mailbox you poll (STEALS: htr-convex
lib/mail/agentmail.ts, lib/mail/svix.ts, lib/mail/inbound.ts). No SDK: four calls and a
signature check do not justify one.

AgentMail delivers mail by webhook (`message.received`), signed with Svix. There is no
working `pull` here: receiving mail needs a webhook route and somewhere durable for an event
to land between the web process and the worker, which is the next piece, not this one.
`send` works today; `pull` says why it doesn't, loudly, rather than silently returning nothing.

Threading rides RFC 5322 `In-Reply-To`/`References` headers on plain `/send`, the same way
`mail_gmail.py` does it, rather than AgentMail's message-id-based `/reply` endpoint: `OutboundMail`
already carries the former, not the latter, and one threading convention across mail adapters
is worth more than using every endpoint a provider offers.

Env: AGENTMAIL_API_KEY, AGENTMAIL_INBOX_ID. Per-tenant secrets (`agentmail_api_key`,
`agentmail_inbox_id`, `agentmail_webhook_secret`) override them, the same way Twilio's do.
`python -m htr.provision.agentmail <tenant>` creates the inbox and webhook and writes
AGENTMAIL_INBOX_ID/_WEBHOOK_ID/_WEBHOOK_SECRET to .env for client zero; a second tenant
wanting its own inbox needs a per-tenant state file the way `retell-agents.yaml` is one for
Retell, not built here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from ..config import Tenant
from ..ports.mail import InboundMail, OutboundMail

API = "https://api.agentmail.to/v0"


def webhook_secret(tenant: Tenant) -> str | None:
    return tenant.secrets.get("agentmail_webhook_secret") or os.environ.get("AGENTMAIL_WEBHOOK_SECRET") or None


class AgentMailMail:
    def __init__(self, secrets: dict[str, str | None] | None = None):
        s = {k: v for k, v in (secrets or {}).items() if v}
        g = os.environ.get
        self.api_key = s.get("agentmail_api_key") or g("AGENTMAIL_API_KEY", "")
        self.inbox_id = s.get("agentmail_inbox_id") or g("AGENTMAIL_INBOX_ID", "")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self.api_key:
            raise RuntimeError("AGENTMAIL_API_KEY not configured")
        r = httpx.request(method, f"{API}{path}",
                           headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                           json=body, timeout=20)
        r.raise_for_status()
        return r.json() if r.text else {}

    # ---- intake ----------------------------------------------------------------

    def pull(self, since: datetime | None = None, limit: int = 50) -> list[InboundMail]:
        raise NotImplementedError(
            "AgentMail delivers mail by webhook, not by polling. Wire /webhooks/{tenant}/mail "
            "(Svix-verified, via svix_verify below) before selecting adapters.mail: agentmail."
        )

    # ---- dispatch ----------------------------------------------------------------

    def send(self, mail: OutboundMail) -> str:
        if not self.inbox_id:
            raise RuntimeError("AGENTMAIL_INBOX_ID not configured")
        headers: dict[str, str] = {}
        if mail.in_reply_to:
            headers["In-Reply-To"] = mail.in_reply_to
            headers["References"] = (mail.references + " " if mail.references else "") + mail.in_reply_to
        body: dict = {"to": [mail.to_address], "subject": mail.subject, "text": mail.text}
        if headers:
            body["headers"] = headers
        r = self._call("POST", f"/inboxes/{quote(self.inbox_id, safe='')}/messages/send", body)
        return str(r.get("message_id", ""))

    # ---- provisioning (STEALS: htr-convex lib/mail/agentmail.ts) ------------------

    def create_inbox(self, username: str, display_name: str | None = None, domain: str | None = None) -> tuple[str, str]:
        """Returns (inbox_id, address)."""
        r = self._call("POST", "/inboxes", {"username": username, "display_name": display_name, "domain": domain})
        return str(r["inbox_id"]), str(r.get("address", r["inbox_id"]))

    def create_webhook(self, url: str, event_types: list[str], inbox_ids: list[str] | None = None) -> tuple[str, str]:
        """Returns (webhook_id, secret). The secret comes back only here: keep it."""
        r = self._call("POST", "/webhooks", {"url": url, "event_types": event_types, "inbox_ids": inbox_ids})
        return str(r["webhook_id"]), str(r["secret"])

    def delete_webhook(self, webhook_id: str) -> None:
        self._call("DELETE", f"/webhooks/{quote(webhook_id, safe='')}")

    def delete_inbox(self, inbox_id: str) -> None:
        self._call("DELETE", f"/inboxes/{quote(inbox_id, safe='')}")


# ---- the webhook payload, once there is a route to hand it to (STEALS: htr-convex lib/mail/inbound.ts) ---

_NAMED = re.compile(r'^"?([^"<]*?)"?\s*<([^<>\s@]+@[^<>\s@]+)>$')


def parse_address(s: str) -> tuple[str | None, str]:
    """'"Sam Lee" <sam@x.com>' -> (name, address); a bare address is itself."""
    raw = (s or "").strip()
    m = _NAMED.match(raw)
    if m:
        return (m.group(1).strip() or None, m.group(2).lower())
    return (None, raw.strip("<>").lower())


_FOOTER = re.compile(r"\s*\n--\s*\nSent via AgentMail\s*$", re.IGNORECASE)


def strip_footer(text: str) -> str:
    """The signature AgentMail appends to what its free inboxes send; not the customer's words."""
    return _FOOTER.sub("", text).strip()


def parse_event(payload: dict) -> tuple[str, InboundMail | None]:
    """An AgentMail `message.received` event, reduced to what the loop needs."""
    event_type = str(payload.get("event_type") or payload.get("type") or "")
    m = payload.get("message")
    if not m:
        return event_type, None
    name, address = parse_address(m.get("from_") or m.get("from") or "")
    raw_ts = m.get("timestamp") or m.get("created_at") or ""
    try:
        received_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if raw_ts else datetime.now(timezone.utc)
    except ValueError:
        received_at = datetime.now(timezone.utc)
    headers = {k.lower(): v for k, v in (m.get("headers") or {}).items()}
    return event_type, InboundMail(
        id=str(m.get("message_id", "")),
        thread_id=str(m.get("thread_id", "")),
        from_address=address,
        from_name=name,
        subject=str(m.get("subject", "")),
        text=strip_footer(str(m.get("text") or m.get("preview") or "")),
        received_at=received_at,
        message_id=headers.get("message-id"),
        references=headers.get("references"),
    )


# ---- Svix: how AgentMail proves a webhook is its own (STEALS: htr-convex lib/mail/svix.ts) -----
#
# HMAC-SHA256 over "<svix-id>.<svix-timestamp>.<body>", keyed by the base64 secret after
# "whsec_", sent as "v1,<base64>" (several may be space-separated).

def svix_sign(secret: str, id_: str, timestamp: str, body: str) -> str:
    raw = secret[6:] if secret.startswith("whsec_") else secret
    key = base64.b64decode(raw)
    digest = hmac.new(key, f"{id_}.{timestamp}.{body}".encode(), hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"


def svix_verify(secret: str, headers: dict[str, str], body: str, now_ms: int | None = None,
                 tolerance_ms: int = 5 * 60_000) -> bool:
    h = {k.lower(): v for k, v in headers.items()}
    id_, ts, sigs = h.get("svix-id"), h.get("svix-timestamp"), h.get("svix-signature")
    if not (id_ and ts and sigs):
        return False
    try:
        ts_ms = int(ts) * 1000
    except ValueError:
        return False
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now - ts_ms) > tolerance_ms:
        return False
    expected = svix_sign(secret, id_, ts, body)
    return any(s.startswith("v1,") and hmac.compare_digest(s, expected) for s in sigs.split(" "))
