"""Twilio SMS via the REST API (STEALS: switchboard, plus inbound signature validation and
API-key auth). No SDK: two endpoints do not justify a dependency.

Credentials come from the tenant's secrets block when present, else the environment:
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN (validates inbound webhooks; may also send),
  TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET (preferred for sending; restricted key),
  TWILIO_FROM_NUMBER.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

import httpx


class TwilioMessaging:
    can_text_callers = True

    def __init__(self, secrets: dict[str, str | None] | None = None):
        s = {k: v for k, v in (secrets or {}).items() if v}
        g = os.environ.get
        self.sid = s.get("twilio_account_sid") or g("TWILIO_ACCOUNT_SID", "")
        self.token = s.get("twilio_auth_token") or g("TWILIO_AUTH_TOKEN", "")
        self.key_sid = s.get("twilio_api_key_sid") or g("TWILIO_API_KEY_SID", "")
        self.key_secret = s.get("twilio_api_key_secret") or g("TWILIO_API_KEY_SECRET", "")
        self.from_number = s.get("twilio_from_number") or g("TWILIO_FROM_NUMBER", "")

    def _auth(self) -> tuple[str, str]:
        if self.key_sid and self.key_secret:
            return (self.key_sid, self.key_secret)
        return (self.sid, self.token)

    def send_sms(self, to: str, body: str) -> str:
        if not (self.sid and self.from_number and all(self._auth())):
            raise RuntimeError("Twilio credentials not configured")
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
            auth=self._auth(),
            data={"To": to, "From": self.from_number, "Body": body},
            timeout=10)
        r.raise_for_status()
        return str(r.json().get("sid", ""))

    # ---- inbound ---------------------------------------------------------------

    @staticmethod
    def signature_for(auth_token: str, url: str, params: dict[str, str]) -> str:
        """Twilio's scheme: HMAC-SHA1 over the full URL + every POST param, sorted by key,
        key and value concatenated, base64-encoded."""
        payload = url + "".join(k + params[k] for k in sorted(params))
        digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
        return base64.b64encode(digest).decode()

    def verify_inbound(self, url: str, params: dict[str, str], signature: str) -> bool:
        if not self.token:            # no token configured: dev only, runs unverified
            return True
        return hmac.compare_digest(self.signature_for(self.token, url, params), signature or "")
