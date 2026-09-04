"""The email channel, offline: a customer writes, a draft appears, the owner rules, the reply
goes out in the thread as the owner. Automated mail never waits on anyone."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi.testclient import TestClient

from htr.adapters.mail_console import ConsoleMail
from htr.adapters.messaging_console import ConsoleMessaging
from htr.config import TenantRegistry
from htr.db import messages
from htr.jobs import drain, enqueue
from htr.ports.mail import InboundMail
from htr.proposals.store import ProposalStore
from htr.web.app import create_app

from conftest import texts_to

OWNER = "+15550000009"


def _registry(tmp_path):
    f = tmp_path / "mail.yaml"
    f.write_text("""
tenants:
  tony:
    display_name: "Tony Myers"
    numbers: ["+15550000001"]
    owner: { name: Tony, phone: "+15550000009", email: tony@example.com, digest_at: immediate }
    adapters: { messaging: none, owner_messaging: console, voice: retell, llm: template, mail: console }
""", encoding="utf-8")
    return TenantRegistry.load(f)


def _mail(id, frm, subject, text, name=None, automated=False, minutes_ago=10):
    return InboundMail(id=id, thread_id=f"t-{id}", from_address=frm, from_name=name, subject=subject, text=text,
                       received_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
                       message_id=f"<{id}@example.com>", automated=automated)


def test_email_in_draft_rule_reply_in_thread(engine, tmp_path):
    registry = _registry(tmp_path)
    ConsoleMail.clear(); ConsoleMessaging.clear()
    ConsoleMail.inbox[:] = [
        _mail("m1", "marco@shop.example", "Leather bag?", "Do you have a leather bag under $200? I need it by Friday.", name="Marco"),
        _mail("m2", "noreply@bank.example", "Your statement", "Your statement is ready.", automated=True),
        _mail("m3", "tony@example.com", "note to self", "buy milk"),
    ]
    client = TestClient(create_app(engine, registry))
    with engine.begin() as conn:
        enqueue(conn, "tony", "mail_pull")
    drain(engine, registry)
    store = ProposalStore(engine)
    (p,) = store.pending("tony")
    assert p.kind == "email" and p.to_number == "marco@shop.example" and p.to_name == "Marco"
    assert p.meta["subject"] == "Leather bag?" and p.meta["message_id"] == "<m1@example.com>"
    assert "Friday" in p.body and p.body.endswith("Tony")
    with engine.connect() as conn:
        rows = conn.execute(sa.select(messages.c.from_number, messages.c.channel).where(messages.c.direction == "in")).all()
    assert ("noreply@bank.example", "email") in rows, "automated mail is recorded"
    assert not any(r[0] == "tony@example.com" for r in rows), "the owner's own mail is not intake"
    digest = next(t for t in texts_to(OWNER) if "waiting on you" in t)
    assert "1 Marco (email, " in digest, "the channel and a deadline label are on the line"

    # replayed pull changes nothing
    with engine.begin() as conn:
        enqueue(conn, "tony", "mail_pull")
    drain(engine, registry)
    assert store.pending_count("tony") == 1

    # the owner rules; the reply goes out as an email in the thread
    client.post("/webhooks/tony/sms", data={"From": OWNER, "To": "+15550000001", "Body": "1 Yes, the Harlow, $185. Want it held?", "MessageSid": "r1"})
    drain(engine, registry)
    sent = store.get(p.id)
    assert sent.status == "sent" and sent.edited
    (out,) = ConsoleMail.sent
    assert out.to_address == "marco@shop.example" and out.subject == "Re: Leather bag?"
    assert out.in_reply_to == "<m1@example.com>" and out.thread_id == "t-m1"
    assert out.text == "Yes, the Harlow, $185. Want it held?"


def test_newer_email_from_same_sender_supersedes(engine, tmp_path):
    registry = _registry(tmp_path)
    ConsoleMail.clear(); ConsoleMessaging.clear()
    ConsoleMail.inbox[:] = [_mail("a1", "sam@x.example", "Q", "Are you open Saturday?", minutes_ago=30)]
    with engine.begin() as conn:
        enqueue(conn, "tony", "mail_pull")
    drain(engine, registry)
    store = ProposalStore(engine)
    (first,) = store.pending("tony")
    ConsoleMail.inbox.append(_mail("a2", "sam@x.example", "Re: Q", "Never mind, found the hours online.", minutes_ago=1))
    with engine.begin() as conn:
        enqueue(conn, "tony", "mail_pull")
    drain(engine, registry)
    (live,) = store.pending("tony")
    assert live.source_id != first.source_id and store.get(first.id).status == "superseded"


def test_mail_off_means_no_pull_and_honest_dispatch_failure(engine, registry, store):
    """The default tenants have no mail adapter: a pull is a no-op, an email proposal fails loudly."""
    with engine.begin() as conn:
        enqueue(conn, "tony", "mail_pull")
    assert drain(engine, registry) == 1
    p = store.propose("tony", "x@y.example", "hi", kind="email", meta={"subject": "s"})
    store.approve(p.id)
    with engine.begin() as conn:
        enqueue(conn, "tony", "dispatch", {"proposal_id": p.id})
    drain(engine, registry)
    assert store.get(p.id).status == "failed" and "no mail channel" in store.get(p.id).error
