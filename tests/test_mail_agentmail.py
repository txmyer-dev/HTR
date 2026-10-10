"""AgentMail: a stranger's webhook proves nothing; Svix's does. Sending works; polling admits
it can't, loudly."""
from __future__ import annotations

import hashlib
import hmac
import base64
import time

import pytest

from htr.adapters.mail_agentmail import (
    AgentMailMail, parse_address, parse_event, strip_footer, svix_sign, svix_verify,
)
from htr.ports.mail import OutboundMail


def _secret_bytes() -> bytes:
    return b"0123456789abcdef0123456789abcdef"


def _secret() -> str:
    return "whsec_" + base64.b64encode(_secret_bytes()).decode()


def _sign(secret: str, id_: str, ts: str, body: str) -> str:
    raw = secret[6:] if secret.startswith("whsec_") else secret
    digest = hmac.new(base64.b64decode(raw), f"{id_}.{ts}.{body}".encode(), hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"


# ---- svix ---------------------------------------------------------------------------------

def test_svix_sign_matches_hand_rolled_reference():
    secret = _secret()
    assert svix_sign(secret, "msg_1", "1700000000", '{"a":1}') == _sign(secret, "msg_1", "1700000000", '{"a":1}')


def test_svix_verify_accepts_a_good_signature_within_the_window():
    secret = _secret()
    ts = "1700000000"
    body = '{"event_type":"message.received"}'
    sig = svix_sign(secret, "msg_1", ts, body)
    headers = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": sig}
    assert svix_verify(secret, headers, body, now_ms=1_700_000_001_000)


def test_svix_verify_rejects_wrong_secret_wrong_body_missing_headers_and_stale_timestamp():
    secret = _secret()
    ts = "1700000000"
    body = '{"event_type":"message.received"}'
    sig = svix_sign(secret, "msg_1", ts, body)
    good = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": sig}
    assert not svix_verify(_secret(), good, body + " ", now_ms=1_700_000_001_000), "body must match byte for byte"
    assert not svix_verify("whsec_" + base64.b64encode(b"different-key-different-key-difx").decode(), good, body, now_ms=1_700_000_001_000)
    assert not svix_verify(secret, {}, body, now_ms=1_700_000_001_000)
    assert not svix_verify(secret, good, body, now_ms=1_700_000_000_000 + 6 * 60_000), "outside the five-minute window"


def test_svix_verify_accepts_one_of_several_space_separated_signatures():
    secret = _secret()
    ts = "1700000000"
    body = "{}"
    sig = svix_sign(secret, "msg_1", ts, body)
    headers = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": f"v2,garbage {sig}"}
    assert svix_verify(secret, headers, body, now_ms=1_700_000_001_000)


def test_svix_verify_defaults_now_to_real_time(monkeypatch):
    secret = _secret()
    ts = str(int(time.time()))
    body = "{}"
    sig = svix_sign(secret, "msg_1", ts, body)
    assert svix_verify(secret, {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": sig}, body)


# ---- inbound parsing ------------------------------------------------------------------------

def test_parse_address_named_and_bare():
    assert parse_address('"Sam Lee" <sam@x.example>') == ("Sam Lee", "sam@x.example")
    assert parse_address("sam@x.example") == (None, "sam@x.example")
    assert parse_address("<Sam@X.example>") == (None, "sam@x.example")


def test_strip_footer_removes_only_the_agentmail_signature():
    assert strip_footer("Yes, Saturdays 9 to 2.\n--\nSent via AgentMail") == "Yes, Saturdays 9 to 2."
    assert strip_footer("no footer here") == "no footer here"


def test_parse_event_reduces_a_message_received_payload():
    payload = {
        "event_type": "message.received",
        "message": {
            "message_id": "m1", "thread_id": "t1", "from_": '"Sam" <sam@x.example>',
            "to": ["tony@inbox.agentmail.to"], "subject": "Q", "text": "Are you open Saturday?\n--\nSent via AgentMail",
            "timestamp": "2026-09-12T03:00:00Z", "headers": {"Message-ID": "<m1@x.example>", "References": "<r0@x.example>"},
        },
    }
    event_type, mail = parse_event(payload)
    assert event_type == "message.received"
    assert mail.id == "m1" and mail.thread_id == "t1"
    assert mail.from_address == "sam@x.example" and mail.from_name == "Sam"
    assert mail.text == "Are you open Saturday?"
    assert mail.message_id == "<m1@x.example>" and mail.references == "<r0@x.example>"


def test_parse_event_with_no_message_is_ignored():
    assert parse_event({"event_type": "inbox.created"}) == ("inbox.created", None)


# ---- send / provisioning (httpx mocked, STEALS-test-pattern: test_stolen_switchboard.py) -----

def test_send_requires_inbox_id():
    with pytest.raises(RuntimeError, match="AGENTMAIL_INBOX_ID"):
        AgentMailMail({"agentmail_api_key": "k"}).send(OutboundMail(to_address="a@b.example", subject="s", text="t"))


def test_send_requires_api_key():
    with pytest.raises(RuntimeError, match="AGENTMAIL_API_KEY"):
        AgentMailMail({"agentmail_inbox_id": "inbox_1"}).send(OutboundMail(to_address="a@b.example", subject="s", text="t"))


def test_send_posts_to_the_inbox_with_threading_headers(monkeypatch):
    seen = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        seen.update(method=method, url=url, headers=headers, json=json)
        class R:
            status_code = 200
            text = '{"message_id": "m2", "thread_id": "t1"}'
            def raise_for_status(self): pass
            def json(self): return {"message_id": "m2", "thread_id": "t1"}
        return R()

    monkeypatch.setattr("htr.adapters.mail_agentmail.httpx.request", fake_request)
    mail = AgentMailMail({"agentmail_api_key": "k", "agentmail_inbox_id": "inbox_1"})
    out = OutboundMail(to_address="sam@x.example", subject="Re: Q", text="Yes, Saturdays.",
                        in_reply_to="<m1@x.example>", references="<r0@x.example>")
    assert mail.send(out) == "m2"
    assert seen["method"] == "POST" and seen["url"].endswith("/inboxes/inbox_1/messages/send")
    assert seen["json"]["to"] == ["sam@x.example"]
    assert seen["json"]["headers"]["In-Reply-To"] == "<m1@x.example>"
    assert seen["json"]["headers"]["References"] == "<r0@x.example> <m1@x.example>"


def test_create_inbox_and_webhook(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url, json))
        class R:
            text = '{}'
            def raise_for_status(self): pass
            def json(self): return body
        if url.endswith("/inboxes"):
            body = {"inbox_id": "inbox_1", "address": "tony@agentmail.to"}
        else:
            body = {"webhook_id": "wh_1", "secret": "whsec_abc"}
        R.json = lambda self: body
        return R()

    monkeypatch.setattr("htr.adapters.mail_agentmail.httpx.request", fake_request)
    mail = AgentMailMail({"agentmail_api_key": "k"})
    inbox_id, address = mail.create_inbox("tony")
    assert (inbox_id, address) == ("inbox_1", "tony@agentmail.to")
    webhook_id, secret = mail.create_webhook("https://example.com/webhooks/tony/mail", ["message.received"], ["inbox_1"])
    assert (webhook_id, secret) == ("wh_1", "whsec_abc")
    assert calls[0][0] == "POST" and calls[0][1].endswith("/inboxes")
    assert calls[1][2]["inbox_ids"] == ["inbox_1"]
