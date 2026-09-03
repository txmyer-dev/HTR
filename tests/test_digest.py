from __future__ import annotations

from datetime import datetime, timedelta, timezone

from htr.digest.render import HELP_LINE, MAX_CHARS, render_digest, segments
from htr.proposals.store import ProposalStore


def test_digest_numbers_lines_and_shows_who_channel_deadline(store: ProposalStore):
    now = datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)
    a = store.propose("tony", "+15551234567", "Yes, the Harlow, $185. Want it held?", to_name="Marco",
                      basis=("does the shop have a leather bag under $200? I need it by Friday",),
                      source_kind="call_message", source_id="1",
                      deadline=datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc))
    b = store.propose("tony", "+15550001111", "Hi there, thanks for your message. Tony will get back to you shortly.",
                      basis=("Are you open Saturday?",), source_kind="sms", source_id="2")
    text = render_digest([(1, a), (2, b)], timezone="America/Chicago", now=now)
    lines = text.split("\n")
    assert lines[0] == "2 waiting on you."
    assert lines[2].startswith('1 Marco (call, Fri): "does the shop have a leather bag') and "→ Yes, the Harlow" in lines[2]
    assert lines[3].startswith('2 +15550001111 (sms): "Are you open Saturday?" →')
    assert lines[-1] == HELP_LINE


def test_digest_caps_length_and_counts_the_tail(store: ProposalStore):
    items = [(n, store.propose("tony", f"+1555000{n:04d}", "x" * 150, basis=("y" * 80,)))
             for n in range(1, 21)]
    text = render_digest(items)
    assert len(text) <= MAX_CHARS
    assert "…and " in text and "more" in text


def test_empty_digest():
    assert render_digest([]) == "Nothing waiting on you."


def test_segments_split_on_lines_and_hard_split_long_lines():
    text = "1 short\n2 " + "a" * 200 + "\n3 short"
    segs = segments(text, limit=160)
    assert all(len(s) <= 160 for s in segs)
    assert segs[0].startswith("1 short") and segs[-1].endswith("3 short")
    assert segments("one\ntwo", 160) == ["one\ntwo"]
