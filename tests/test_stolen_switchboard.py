"""One test per governing rule, plus the slice-8 acceptance test."""
from __future__ import annotations

import sqlalchemy as sa

from htr.adapters.messaging_console import ConsoleMessaging
from htr.config import TenantRegistry
from htr.db import actions, appointments, calls, events, jobs as jobs_t, messages
from htr.jobs import run_pending

from conftest import ended_body, tool_body


# ---------------------------------------------------------------- slice 1/2

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["tenants"] == 2


def test_call_ingestion_lands_in_spine(client, engine):
    client.post("/webhooks/tony/tool", json=tool_body("lookup_caller"))
    with engine.connect() as conn:
        call = conn.execute(sa.select(calls)).mappings().one()
        kinds = conn.execute(sa.select(events.c.kind).order_by(events.c.id)).scalars().all()
    assert call["tenant_id"] == "tony" and call["from_number"] == "+15551234567"
    assert kinds == ["call.started", "tool.lookup_caller"]


def test_unknown_tenant_404(client):
    assert client.post("/webhooks/nobody/tool", json=tool_body("lookup_caller")).status_code == 404


# ---------------------------------------------------------------- slice 3: reads in budget

def test_check_availability_returns_slots(client):
    r = client.post("/webhooks/tony/tool",
                    json=tool_body("check_availability", {"day": "2026-08-24T00:00:00+00:00"}))
    body = r.json()
    assert body["available"] and len(body["slots"]) == 3
    assert body["slots"][0]["start"].endswith("09:00:00+00:00")
    assert body["elapsed_ms"] < 800          # the budget, measured not assumed


# ---------------------------------------------------------------- slice 4: R4 idempotency

def test_replayed_webhook_does_not_double_book(client, engine):
    body = tool_body("book_appointment", {"slot_start": "2026-08-24T09:00:00+00:00"})
    first = client.post("/webhooks/tony/tool", json=body).json()
    replay = client.post("/webhooks/tony/tool", json=body).json()
    assert first["booked"] and "reference" in first
    assert replay["booked"] and replay.get("duplicate")
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(appointments)).scalar() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(actions)).scalar() == 1


def test_take_message_stores_once(client, engine):
    body = tool_body("take_message", {"message": "call me about the fence"})
    client.post("/webhooks/tony/tool", json=body)
    client.post("/webhooks/tony/tool", json=body)
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(messages)).scalar() == 1


def test_send_link_texts_once(client):
    body = tool_body("send_link")
    client.post("/webhooks/tony/tool", json=body)
    client.post("/webhooks/tony/tool", json=body)
    assert ConsoleMessaging.sent == [("+15551234567", "Tony Myers: https://cal.example/tony")]


# ---------------------------------------------------------------- slice 5: post-call path

def test_call_ended_runs_post_call_once(client, engine, registry):
    client.post("/webhooks/tony/tool", json=tool_body("take_message", {"message": "quote please"}))
    client.post("/webhooks/tony/call-ended", json=ended_body())
    dup = client.post("/webhooks/tony/call-ended", json=ended_body()).json()
    assert dup["duplicate"]
    with engine.connect() as conn:
        pending = conn.execute(sa.select(sa.func.count()).select_from(jobs_t)
                               .where(jobs_t.c.kind == "post_call")).scalar()
    assert pending == 1                       # duplicate webhook did not enqueue twice
    assert run_pending(engine, registry) == 1
    owner_texts = [s for s in ConsoleMessaging.sent if s[0] == "+15550000009"]
    assert len(owner_texts) == 1 and "quote please" in owner_texts[0][1]
    with engine.connect() as conn:            # contact upserted post-call
        from htr.db import contacts
        c = conn.execute(sa.select(contacts)).mappings().one()
    assert c["phone"] == "+15551234567" and c["call_count"] == 1


# ---------------------------------------------------------------- slice 6: R3 degrade + heartbeat

def test_adapter_failure_degrades_to_human(client, engine, monkeypatch):
    from htr import adapters
    def broken(*a, **k):
        raise RuntimeError("calendar is down")
    monkeypatch.setattr(adapters, "calendar_for", broken)
    r = client.post("/webhooks/tony/tool", json=tool_body("check_availability")).json()
    assert r["degraded"] and "call you right back" in r["speech"]
    with engine.connect() as conn:            # the caller's need was recorded as a message
        assert conn.execute(sa.select(sa.func.count()).select_from(messages)).scalar() == 1
        kinds = conn.execute(sa.select(events.c.kind)).scalars().all()
    assert "tool.degraded" in kinds


def test_heartbeat_pages_operator_on_failure(engine, registry, monkeypatch):
    import os
    from htr import adapters as ad
    from htr.jobs import enqueue
    ConsoleMessaging.clear()
    monkeypatch.setenv("HTR_OPERATOR_PHONE", "+15559999999")
    real = ad.calendar_for
    def broken(tenant, engine_):
        raise RuntimeError("calendar is down")
    monkeypatch.setattr(ad, "calendar_for", broken)
    with engine.begin() as conn:
        enqueue(conn, "tony", "heartbeat")
    run_pending(engine, registry)
    pages = [s for s in ConsoleMessaging.sent if s[0] == "+15559999999"]
    assert pages and "heartbeat FAILED" in pages[0][1]


# ---------------------------------------------------------------- slice 7: call log

def test_call_log_page(client):
    client.post("/webhooks/tony/tool", json=tool_body("take_message", {"message": "hello"}))
    html = client.get("/tenants/tony/calls").text
    assert "Tony Myers" in html and "+15551234567" in html and "hello" in html


# ---------------------------------------------------------------- slice 8: acceptance test (R1)

def test_second_tenant_runs_on_config_alone(client, engine, registry):
    """The tenant 'second' exists only in tenants.yaml. Same endpoints, same code paths."""
    b = tool_body("check_availability", {"day": "2026-08-29T00:00:00+00:00"},  # a Saturday
                  call_id="call-2", to_number="+15550000002")
    r = client.post("/webhooks/second/tool", json=b).json()
    assert r["available"]                     # second's hours include Saturday; tony's don't
    assert r["slots"][0]["start"].endswith("08:00:00+00:00")   # and start at 08:00, not 09:00

    client.post("/webhooks/second/call-ended",
                json=ended_body(call_id="call-2", to_number="+15550000002"))
    assert run_pending(engine, registry) >= 1
    owner_texts = [s for s in ConsoleMessaging.sent if s[0] == "+15550000019"]
    assert len(owner_texts) == 1              # second's owner, from second's config

    with engine.connect() as conn:
        tenants_seen = set(conn.execute(sa.select(calls.c.tenant_id)).scalars().all())
    assert tenants_seen == {"tony", "second"} or tenants_seen == {"second"}


def test_webhook_secret_enforced_when_configured(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("SB_TEST_SECRET", "s3cret")
    f = tmp_path / "t.yaml"
    f.write_text("""
tenants:
  locked:
    display_name: "Locked"
    numbers: ["+15550000003"]
    owner_phone: "+15550000029"
    adapters: { voice: retell, messaging: console }
    secrets: { voice_webhook_secret: SB_TEST_SECRET }
""")
    from fastapi.testclient import TestClient
    from htr.web.app import create_app
    c = TestClient(create_app(engine, TenantRegistry.load(f)))
    assert c.post("/webhooks/locked/tool", json=tool_body("lookup_caller")).status_code == 401
    ok = c.post("/webhooks/locked/tool", json=tool_body("lookup_caller"),
                headers={"x-retell-secret": "s3cret"})
    assert ok.status_code == 200


# ---------------------------------------------------------------- slice 9: provisioning

def test_prompt_renders_from_tenant_config(registry):
    from htr.provision import render_prompt
    text = render_prompt(registry.get("tony"))
    assert "Tony Myers" in text and "How can I help?" in text
    leftover = text.replace("{{current_time}}", "")   # retell's own variable stays literal
    assert "{" not in leftover                         # no unfilled template slots


def test_tools_target_the_tenant_webhook(registry):
    from htr.provision import build_tools
    from htr.tools import TOOLS_V1
    tools = build_tools(registry.get("second"), "https://sb.example.com")
    customs = {t["name"]: t for t in tools if t["type"] == "custom"}
    assert set(customs) == set(TOOLS_V1)                      # the frozen surface, exactly
    assert all(t["url"] == "https://sb.example.com/webhooks/second/tool"
               for t in customs.values())
    # placeholder escalation number -> the built-in transfer is skipped, loudly, not sent broken
    assert not [t for t in tools if t["type"] == "transfer_call"]
    import dataclasses
    real = dataclasses.replace(registry.get("second"), escalation_phone="+15122001000")
    transfer = next(t for t in build_tools(real, "https://sb.example.com")
                    if t["type"] == "transfer_call")
    assert transfer["transfer_destination"]["number"] == "+15122001000"


def test_provisioning_is_per_tenant_config_only(registry):
    """Slice 9 inherits slice 8's rule: a new tenant's agent is a render, not code."""
    from htr.provision import llm_payload
    a = llm_payload(registry.get("tony"), "https://sb.example.com")
    b = llm_payload(registry.get("second"), "https://sb.example.com")
    assert "Tony Myers" in a["general_prompt"] and "Second Business" in b["general_prompt"]
    assert a["general_tools"] != b["general_tools"]           # URLs and transfer differ


# ---------------------------------------------------------------- voice-only messaging (ntfy)

def test_send_link_declines_honestly_without_sms(client, engine, registry, monkeypatch):
    """A Retell-owned (voice-only) number cannot text callers: send_link must not pretend."""
    from htr import adapters
    from htr.adapters.messaging_ntfy import NtfyMessaging
    monkeypatch.setattr(adapters, "messaging_for", lambda t: NtfyMessaging())
    r = client.post("/webhooks/tony/tool", json=tool_body("send_link")).json()
    assert r["sent"] is False and "cal.example/tony" in r["speech"]
    with engine.connect() as conn:
        body = conn.execute(sa.select(messages.c.body)).scalar()
    assert "booking link" in body                      # the want reached the message log


def test_ntfy_posts_to_topic(monkeypatch):
    from htr.adapters.messaging_ntfy import NtfyMessaging
    sent = {}
    class R:
        def raise_for_status(self): pass
    def fake_post(url, content=None, headers=None, timeout=None):
        sent.update(url=url, body=content, headers=headers); return R()
    monkeypatch.setenv("HTR_NTFY_TOPIC", "tony-sb-test")
    monkeypatch.setattr("htr.adapters.messaging_ntfy.httpx.post", fake_post)
    NtfyMessaging().send_sms("+15550000009", "call summary here")
    assert sent["url"].endswith("/tony-sb-test") and b"summary" in sent["body"]


# ---------------------------------------------------------------- first-call fixes

def test_past_day_snaps_to_same_weekday_ahead(client):
    """Live bug: the model asked for 'this Friday' as 2024-06-14. Never offer history."""
    r = client.post("/webhooks/tony/tool",
                    json=tool_body("check_availability", {"day": "2024-06-12"})).json()
    from datetime import date
    slot_date = date.fromisoformat(r["slots"][0]["start"][:10]) if r["slots"] else None
    assert slot_date is None or slot_date >= date.today()


def test_voice_only_prompt_forbids_texting(registry):
    import dataclasses
    from htr.provision import render_prompt
    tony = registry.get("tony")
    tony = dataclasses.replace(tony, adapters={**tony.adapters, "messaging": "ntfy"})
    text = render_prompt(tony)                          # voice-only line: can't text
    assert "cannot send text messages" in text and "send_link" not in text
    assert "{{current_time}}" in text                   # the model gets a clock
    assert "ring you back at this number" in text       # total-failure honesty
