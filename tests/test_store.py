"""STEALS: waggle src/proposals/store.test.ts, ported to the table-backed store."""
from __future__ import annotations

from htr.proposals.store import PENDING, ProposalStore


def test_approve_signs_once_and_only_from_pending(store: ProposalStore):
    p = store.propose("tony", "+15551234567", "hello", to_name="Sam")
    assert p.id == 1 and p.status == PENDING and "Sam" in p.summary
    signed = store.approve(p.id)
    assert signed and signed.status == "signed" and signed.ruled_at is not None
    assert store.approve(p.id) is None, "approving again is a no-op"
    assert store.begin_send(p.id).status == "sending"
    assert store.mark_sent(p.id, "SM123").status == "sent"
    assert store.get(p.id).sent_ref == "SM123"


def test_reject_never_signs(store: ProposalStore):
    p = store.propose("tony", "+15551234567", "hello")
    assert store.reject(p.id).status == "rejected"
    assert store.approve(p.id) is None
    assert store.begin_send(p.id) is None


def test_edit_changes_the_draft_before_signing(store: ProposalStore):
    p = store.propose("tony", "+15551234567", "draft", to_name="Sam")
    e = store.edit(p.id, "edited")
    assert e.body == "edited" and e.edited and "edited" in e.summary
    store.approve(p.id)
    assert store.edit(p.id, "too late") is None, "only pending proposals are editable"
    assert store.get(p.id).body == "edited"


def test_failed_send_allows_retry(store: ProposalStore):
    p = store.propose("tony", "+15551234567", "hello")
    store.approve(p.id)
    store.begin_send(p.id)
    f = store.mark_failed(p.id, "carrier said no")
    assert f.status == "failed" and "carrier" in f.error
    r = store.retry(p.id)
    assert r.status == "signed" and r.error is None
    assert store.begin_send(p.id).status == "sending"


def test_pending_count_and_source_uniqueness(store: ProposalStore):
    store.propose("tony", "+1", "a", source_kind="sms", source_id="m1")
    store.propose("tony", "+2", "b", source_kind="sms", source_id="m2")
    store.propose("second", "+3", "c")
    assert store.pending_count("tony") == 2 and store.pending_count("second") == 1
    assert store.by_source("tony", "sms", "m1").body == "a"
    import pytest
    import sqlalchemy as sa
    with pytest.raises(sa.exc.IntegrityError):
        store.propose("tony", "+1", "again", source_kind="sms", source_id="m1")
