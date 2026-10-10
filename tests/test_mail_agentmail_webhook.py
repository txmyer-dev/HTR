"""AgentMail intake: a webhook, not a poll. A stranger's POST proves nothing without Svix; the
owner's own address never drafts; a person becomes a draft job, once."""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from htr.adapters.mail_agentmail import svix_sign
from htr.config import TenantRegistry
from htr.jobs import drain
from htr.proposals.store import ProposalStore
from htr.web.app import create_app

OWNER_EMAIL = "tony@example.com"


def _registry(tmp_path):
    """AGENTMAIL_WEBHOOK_SECRET (the env fallback every tenant shares) is what the tests set;
    a per-tenant `secrets: { agentmail_webhook_secret: ... }` override is exercised nowhere here
    because it is the same lookup Twilio's own secret already has coverage for."""
    f = tmp_path / "install.yaml"
    f.write_text(f"""
tenants:
  tony:
    display_name: "Tony Myers"
    numbers: ["+15550000001"]
    owner: {{ name: Tony, phone: "+15550000009", email: {OWNER_EMAIL}, digest_at: immediate }}
    adapters: {{ messaging: console, voice: retell, llm: template, mail: agentmail }}
""", encoding="utf-8")
    return f


def _message_event(from_addr: str, text: str, message_id: str = "m1", thread_id: str = "t1") -> dict:
    return {"event_type": "message.received",
            "message": {"message_id": message_id, "thread_id": thread_id, "from_": from_addr,
                       "to": ["tony@inbox.agentmail.to"], "subject": "Q", "text": text,
                       "timestamp": "2026-09-13T12:00:00Z"}}


def _post_signed(client, path, payload, secret_str, ts=None):
    body = json.dumps(payload)
    ts = ts or str(int(time.time()))
    sig = svix_sign(secret_str, "msg-1", ts, body)
    return client.post(path, content=body, headers={
        "content-type": "application/json", "svix-id": "msg-1", "svix-timestamp": ts, "svix-signature": sig})


def test_unsigned_request_is_refused_when_a_secret_is_configured(tmp_path, engine, monkeypatch):
    monkeypatch.setenv("AGENTMAIL_WEBHOOK_SECRET", "whsec_c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0IQ==")
    f = _registry(tmp_path)
    client = TestClient(create_app(engine, TenantRegistry.load(f)))
    r = client.post("/webhooks/tony/mail", json=_message_event("sam@x.example", "Hi"))
    assert r.status_code == 401


def test_correctly_signed_person_mail_becomes_a_draft(tmp_path, engine, monkeypatch):
    secret_str = "whsec_c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0IQ=="
    monkeypatch.setenv("AGENTMAIL_WEBHOOK_SECRET", secret_str)
    f = _registry(tmp_path)
    registry = TenantRegistry.load(f)
    client = TestClient(create_app(engine, registry))
    r = _post_signed(client, "/webhooks/tony/mail", _message_event("sam@x.example", "Are you open Saturday?"), secret_str)
    assert r.status_code == 200 and r.json()["ok"] is True
    drain(engine, registry)
    (p,) = ProposalStore(engine).pending("tony")
    assert p.kind == "email" and p.to_number == "sam@x.example"

    # a replay (same message id, same idempotency key) does nothing new
    _post_signed(client, "/webhooks/tony/mail", _message_event("sam@x.example", "Are you open Saturday?"), secret_str)
    drain(engine, registry)
    assert ProposalStore(engine).pending_count("tony") == 1


def test_owners_own_mail_never_drafts(tmp_path, engine, monkeypatch):
    secret_str = "whsec_c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0IQ=="
    monkeypatch.setenv("AGENTMAIL_WEBHOOK_SECRET", secret_str)
    f = _registry(tmp_path)
    registry = TenantRegistry.load(f)
    client = TestClient(create_app(engine, registry))
    _post_signed(client, "/webhooks/tony/mail", _message_event(OWNER_EMAIL, "note to self"), secret_str)
    drain(engine, registry)
    assert ProposalStore(engine).pending_count("tony") == 0


def test_a_non_message_event_is_acknowledged_and_ignored(tmp_path, engine, monkeypatch):
    secret_str = "whsec_c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0IQ=="
    monkeypatch.setenv("AGENTMAIL_WEBHOOK_SECRET", secret_str)
    f = _registry(tmp_path)
    client = TestClient(create_app(engine, TenantRegistry.load(f)))
    r = _post_signed(client, "/webhooks/tony/mail", {"event_type": "inbox.created"}, secret_str)
    assert r.status_code == 200 and r.json() == {"ok": True, "ignored": "inbox.created"}


def test_no_secret_configured_runs_unverified_dev_only(tmp_path, engine):
    """Same escape hatch as Twilio's verify_inbound: no secret, no check. Dev/test only."""
    f = _registry(tmp_path)
    registry = TenantRegistry.load(f)
    client = TestClient(create_app(engine, registry))
    r = client.post("/webhooks/tony/mail", json=_message_event("sam@x.example", "hi"))
    assert r.status_code == 200
    drain(engine, registry)
    assert ProposalStore(engine).pending_count("tony") == 1
