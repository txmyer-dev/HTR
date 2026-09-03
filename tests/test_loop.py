"""The whole loop, offline: a call comes in, something waits, the owner rules by text, it goes out.

Every test here runs against the console adapters and the template drafter, exactly as client
zero would before any account exists."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from htr.adapters.messaging_console import ConsoleMessaging
from htr.db import actions, messages, proposals as proposals_t, rulings as rulings_t
from htr.jobs import drain, make_rulings
from htr.proposals.store import ProposalStore

from conftest import CALLER, OWNER, ended_body, sms_form, texts_to, tool_body


def _take_message_and_end_call(client, body="Do you have a leather bag under $200? I need it by Friday.",
                               call_id="call-1", from_number=CALLER):
    client.post("/webhooks/tony/tool", json=tool_body("take_message", {"message": body},
                                                       call_id=call_id, from_number=from_number))
    client.post("/webhooks/tony/call-ended", json=ended_body(call_id=call_id, from_number=from_number))


# ---------------------------------------------------------------- the happy path

def test_call_to_digest_to_ruling_to_send(client, engine, registry, store):
    _take_message_and_end_call(client)
    drain(engine, registry)                       # post_call -> draft -> digest (immediate)

    # a proposal exists, with the caller's words as its basis and Friday as its deadline
    (p,) = store.pending("tony")
    assert p.to_number == CALLER and p.source_kind == "call_message"
    assert p.basis[0].startswith("Do you have a leather bag")
    assert p.deadline is not None and p.digest_no == 1

    # the owner got the summary (kept from switchboard) and the digest
    owner_texts = texts_to(OWNER)
    assert any("Message: Do you have a leather bag" in t for t in owner_texts)
    digest = next(t for t in owner_texts if t.startswith("1 waiting on you."))
    assert f"1 {CALLER} (call, Fri)" in digest and "leather bag" in digest

    # nothing has gone to the caller yet
    assert texts_to(CALLER) == []

    # the owner rules from their phone
    r = client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 ✅"))
    assert r.status_code == 200 and "<Response>" in r.text
    assert store.get(p.id).status == "signed"
    assert texts_to(OWNER)[-1].startswith("Sending 1 to")

    drain(engine, registry)                       # dispatch
    sent = store.get(p.id)
    assert sent.status == "sent" and sent.sent_ref
    assert texts_to(CALLER) == [p.body]
    with engine.connect() as conn:
        act = conn.execute(sa.select(actions).where(actions.c.kind == "send_sms")).mappings().one()
        assert act["idempotency_key"] == f"proposal:{p.id}:send"
        out = conn.execute(sa.select(messages).where(messages.c.direction == "out")).mappings().one()
        assert out["from_number"] == CALLER and out["body"] == p.body
        verdicts = conn.execute(sa.select(rulings_t.c.verdict)).scalars().all()
    assert verdicts == ["sign"]

    # a replayed ruling and a replayed dispatch change nothing
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 ✅"))
    drain(engine, registry)
    assert texts_to(CALLER) == [p.body]
    assert "nothing waiting under that number" in texts_to(OWNER)[-1]


def test_reject_and_edit(client, engine, registry, store):
    _take_message_and_end_call(client, "Can you quote the fence?", "call-1", "+15550002222")
    _take_message_and_end_call(client, "Are you open Saturday", "call-2", "+15550003333")
    drain(engine, registry)
    a, b = store.pending("tony")
    assert (a.digest_no, b.digest_no) == (1, 2)

    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 no\n2 Yes, 9 to 1. Come by."))
    assert store.get(a.id).status == "rejected"
    b2 = store.get(b.id)
    assert b2.status == "signed" and b2.edited and b2.body == "Yes, 9 to 1. Come by."
    confirmation = texts_to(OWNER)[-1]
    assert "Skipped 1" in confirmation and "Sending 2" in confirmation and "with your words" in confirmation

    drain(engine, registry)
    assert texts_to("+15550002222") == []
    assert texts_to("+15550003333") == ["Yes, 9 to 1. Come by."]
    with engine.connect() as conn:
        edit = conn.execute(sa.select(rulings_t.c.edit_body).where(rulings_t.c.verdict == "edit")).scalar()
    assert edit == "Yes, 9 to 1. Come by."


# ---------------------------------------------------------------- the invariants

def test_a_stranger_cannot_rule(client, engine, registry, store):
    _take_message_and_end_call(client)
    drain(engine, registry)
    (p,) = store.pending("tony")
    client.post("/webhooks/tony/sms", data=sms_form("+15559990000", "1 ✅", sid="SM-stranger"))
    assert store.get(p.id).status == "pending"
    drain(engine, registry)
    assert texts_to(CALLER) == []
    # the stranger's text is an inbound message, not a ruling attempt: no ruling row, one message row
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(rulings_t)).scalar() == 0
        body = conn.execute(sa.select(messages.c.body).where(messages.c.from_number == "+15559990000")).scalar()
    assert body == "1 ✅"


def test_a_stale_digest_cannot_approve_a_newer_proposal(client, engine, registry, store):
    """Proposal A is #1 in digest 1 and gets rejected. B arrives. Until a new digest numbers B,
    '1 ✅' must not approve it."""
    _take_message_and_end_call(client, "first", "call-1", "+15550002222")
    drain(engine, registry)
    (a,) = store.pending("tony")
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 no"))
    assert store.get(a.id).status == "rejected"

    # B is created but the immediate digest job is what numbers it; block that by draining only post_call+draft
    _take_message_and_end_call(client, "second", "call-2", "+15550003333")
    from htr.jobs import run_pending
    run_pending(engine, registry)                 # post_call
    run_pending(engine, registry)                 # draft (enqueues digest, not yet run)
    (b,) = store.pending("tony")
    assert b.digest_no is None

    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 ✅"))
    assert store.get(b.id).status == "pending"
    assert "nothing waiting under that number" in texts_to(OWNER)[-1]

    drain(engine, registry)                       # now the digest numbers B as 1
    assert store.get(b.id).digest_no == 1
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 ✅"))
    assert store.get(b.id).status == "signed"


def test_all_later_hold_and_question_mark(client, engine, registry, store):
    _take_message_and_end_call(client, "one", "call-1", "+15550002222")
    _take_message_and_end_call(client, "two", "call-2", "+15550003333")
    drain(engine, registry)
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "later"))
    assert "Left 2 waiting" in texts_to(OWNER)[-1]
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "hold until 9"))
    assert "Holding the room until 9" in texts_to(OWNER)[-1]
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "?"))
    assert texts_to(OWNER)[-1].startswith("2 waiting on you.")
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "all"))
    assert all(p.status == "signed" for p in store.with_status("tony", "signed")) and store.pending_count("tony") == 0
    drain(engine, registry)
    assert texts_to("+15550002222") and texts_to("+15550003333")
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "?"))
    assert texts_to(OWNER)[-1] == "Nothing waiting on you."


def test_nonsense_gets_help_not_a_guess(client, engine, registry):
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "what does this mean"))
    assert "I read replies like" in texts_to(OWNER)[-1]


# ---------------------------------------------------------------- inbound texts

def test_inbound_text_becomes_a_proposal_and_a_second_text_supersedes_it(client, engine, registry, store):
    client.post("/webhooks/tony/sms", data=sms_form("+15550004444", "Are you open Saturday?", "SM-a"))
    client.post("/webhooks/tony/sms", data=sms_form("+15550004444", "Are you open Saturday?", "SM-a"))   # replay
    drain(engine, registry)
    (p,) = store.pending("tony")
    assert p.source_kind == "sms" and "Are you open Saturday" in p.basis[0]
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(messages)).scalar() == 1

    # a newer text from the same number supersedes the older pending draft
    client.post("/webhooks/tony/sms", data=sms_form("+15550004444", "Never mind, found it online", "SM-b"))
    drain(engine, registry)
    (newest,) = store.pending("tony")
    assert newest.source_id == "2" and "Never mind" in newest.basis[0]
    assert store.get(p.id).status == "superseded"
    # and the owner's next digest lists only the live one
    assert "Never mind" in texts_to(OWNER)[-1] and "Are you open" not in texts_to(OWNER)[-1]


# ---------------------------------------------------------------- expiry

def test_overdue_draft_expires_tells_the_counterparty_and_relists(client, engine, registry, store):
    _take_message_and_end_call(client, "need this by Friday", "call-1", "+15550002222")
    drain(engine, registry)
    (p,) = store.pending("tony")
    r = make_rulings(engine)
    r.now = lambda: datetime.now(timezone.utc) + timedelta(days=30)
    expired = r.expire_pass(registry.get("tony"))
    assert expired == [p.id]
    assert store.get(p.id).status == "expired"
    notice = texts_to("+15550002222")
    assert len(notice) == 1 and "will reply personally" in notice[0]
    (relisted,) = store.pending("tony")
    assert relisted.source_kind == "relist" and relisted.body == p.body and relisted.deadline is None
    # a second pass changes nothing
    assert r.expire_pass(registry.get("tony")) == []
    assert texts_to("+15550002222") == notice


# ---------------------------------------------------------------- the worker clock

def test_digest_is_due_once_per_local_day(engine, registry, store, monkeypatch):
    from htr.worker import digest_due
    import dataclasses
    tony = registry.get("tony")
    owner = dataclasses.replace(tony.owner, digest_at="17:00")
    tony = dataclasses.replace(tony, owner=owner)
    at_4pm = datetime(2026, 9, 3, 21, 0, tzinfo=timezone.utc)       # 16:00 Chicago
    at_6pm = datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)       # 18:00 Chicago
    assert not digest_due(engine, tony, at_6pm), "nothing pending"
    store.propose("tony", "+1", "x")
    assert not digest_due(engine, tony, at_4pm)
    assert digest_due(engine, tony, at_6pm)
    make_rulings(engine).send_digest(tony)
    assert not digest_due(engine, tony, at_6pm), "already sent today"
    store.propose("tony", "+2", "y")
    next_day = at_6pm + timedelta(days=1)
    assert digest_due(engine, tony, next_day)
