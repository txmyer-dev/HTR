"""STEALS: waggle src/tools/waiting.test.ts, ported with the module."""
from __future__ import annotations

from datetime import datetime, timezone

from htr.reader.deadline import find_deadline
from htr.reader.waiting import Me, Msg, Thread, latest_unanswered, waiting_on_me

ME, SAM = "me", "sam"


def m(id, sender, content, t, reply_to=None, root_id=None):
    return Msg(id, sender, content, t, reply_to, root_id)


def test_finds_mentions_replies_and_open_questions_and_drops_what_i_answered():
    items = waiting_on_me(
        [Thread("g", (
            m("q1", SAM, "Does anyone know the release date?", 10),
            m("q2", SAM, "Tony, can you look at #412?", 20),
            m("mine1", ME, "Sure, looking.", 25, reply_to="q2", root_id="q2"),
            m("q3", SAM, "Where is the retro doc?", 30),
            m("r1", SAM, "Thanks for the fix!", 40, reply_to="mine0", root_id="mine0"),
            m("mine0", ME, "Fixed the pager.", 5),
            m("s1", SAM, "Shipping at noon.", 50),
            m("q4", SAM, "Gift question: does the shop have a leather bag under $200? I need it by Friday.", 60),
        ), channel_name="general")],
        Me(frozenset({ME}), ("Tony", "npub1tony")),
    )
    by_id = {i.msg.id: i.reason for i in items}
    assert by_id["q1"] == "question"
    assert "q2" not in by_id, "answered mention is not waiting"
    assert by_id["q3"] == "question"
    assert by_id["r1"] == "reply-to-you"
    assert "s1" not in by_id, "a statement is not waiting"
    assert by_id["q4"] == "question", "a question mark mid-message still counts"
    assert items[0].msg.id == "q4", "newest first"
    assert items[0].channel_name == "general"


def test_since_and_limit_are_honoured_and_short_names_never_match():
    items = waiting_on_me(
        [Thread("g", (m("a", SAM, "Is it ready?", 10), m("b", SAM, "Is it done?", 20), m("c", SAM, "Hi", 30)))],
        Me(frozenset({ME}), ("Hi",)), since=15, limit=1)
    assert [i.msg.id for i in items] == ["b"]


def test_sms_thread_waits_until_something_goes_out():
    inbound = [m("1", SAM, "Are you open Saturday?", 10)]
    assert latest_unanswered(inbound, []).id == "1"
    assert latest_unanswered(inbound, [m("o", "us", "Yes, 9 to 1.", 12)]) is None
    inbound.append(m("2", SAM, "Great, see you then", 15))
    assert latest_unanswered(inbound, [m("o", "us", "Yes, 9 to 1.", 12)]).id == "2"
    assert latest_unanswered([], []) is None


def test_deadline_reader_finds_a_named_day():
    now = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)          # a Thursday
    d, hint = find_deadline("I need it by Friday", now, "America/New_York")
    assert hint == "Friday" and d.astimezone(timezone.utc).date().isoformat() == "2026-09-04"
    d, hint = find_deadline("can you do it tomorrow?", now, "UTC")
    assert hint == "tomorrow" and d.date().isoformat() == "2026-09-04"
    assert find_deadline("no rush at all", now) == (None, None)
    d, _ = find_deadline("thursday works", now, "UTC")                # said on a Thursday: next week
    assert d.date().isoformat() == "2026-09-10"
