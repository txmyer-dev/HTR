"""Retell webhook shapes -> neutral ToolCall / CallEnded (STEALS: switchboard, plus real Retell
verification). The only file that knows Retell's JSON.

Two ways a request proves it is Retell's:
- Custom tool calls carry whatever headers we set at provisioning: `x-retell-secret`.
- Webhook events (call_ended and friends) carry `X-Retell-Signature: v=<ms>,d=<hex>`, where
  d = HMAC-SHA256(raw_body + timestamp, api_key). Verified over the raw body, within 5 minutes.
When neither is present, `confirm_call` asks Retell whether the call exists and matches; the
call-ended route uses that as a backstop so a signature mangled in transit still cannot let a
stranger enqueue work for a call that never happened.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any

import httpx

from ..ports.voice import CallEnded, ToolCall

RETELL_BASE = "https://api.retellai.com"
_SIG = re.compile(r"v=(\d+),d=([0-9a-f]+)")
SIGNATURE_WINDOW_MS = 5 * 60 * 1000


def _header(headers: dict[str, str], name: str) -> str:
    low = {k.lower(): v for k, v in headers.items()}
    return low.get(name.lower(), "") or ""


class RetellVoice:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("RETELL_API_KEY", "")

    def verify(self, secret: str | None, headers: dict[str, str], raw_body: bytes) -> bool:
        if not secret and not self.api_key:      # nothing to verify against: dev only
            return True
        supplied = _header(headers, "x-retell-secret")
        if secret and supplied and hmac.compare_digest(supplied, secret):
            return True
        return self.verify_signature(_header(headers, "x-retell-signature"), raw_body)

    def verify_signature(self, signature: str, raw_body: bytes, now_ms: int | None = None) -> bool:
        m = _SIG.match(signature or "")
        if not m or not self.api_key:
            return False
        ts, digest = m.group(1), m.group(2)
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if abs(now - int(ts)) > SIGNATURE_WINDOW_MS:
            return False
        expected = hmac.new(self.api_key.encode(), raw_body + ts.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, digest)

    def confirm_call(self, call_id: str, from_number: str = "", to_number: str = "") -> bool:
        """Backstop: does Retell know this call, from and to these numbers?"""
        if not (self.api_key and call_id):
            return False
        try:
            r = httpx.get(f"{RETELL_BASE}/v2/get-call/{call_id}",
                          headers={"Authorization": f"Bearer {self.api_key}"}, timeout=8)
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        c = r.json()
        return (not from_number or c.get("from_number") == from_number) and \
               (not to_number or c.get("to_number") == to_number)

    def parse_tool_call(self, body: dict[str, Any]) -> ToolCall:
        call = body.get("call", {})
        return ToolCall(
            call_id=call.get("call_id", ""),
            tool_call_id=body.get("tool_call_id") or body.get("name", "") + ":0",
            name=body.get("name", ""),
            arguments=body.get("args", {}) or {},
            from_number=call.get("from_number", ""),
            to_number=call.get("to_number", ""),
        )

    def parse_call_ended(self, body: dict[str, Any]) -> CallEnded:
        call = body.get("call", body)
        return CallEnded(
            call_id=call.get("call_id", ""),
            from_number=call.get("from_number", ""),
            to_number=call.get("to_number", ""),
            transcript=call.get("transcript", "") or "",
            metadata={k: call.get(k) for k in ("disconnection_reason", "call_analysis", "event") if k in call},
        )
