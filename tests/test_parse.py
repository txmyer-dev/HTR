"""STEALS: waggle src/rulings/rulings.test.ts (the parser cases), ported and extended for SMS."""
from __future__ import annotations

from htr.rulings.parse import Verdict, parse_reply, verdict_from_mark, verdict_from_reply


def test_marks_sign_reject_and_ignore():
    assert verdict_from_mark("✅") == Verdict("sign")
    assert verdict_from_mark("✔️") == Verdict("sign")
    assert verdict_from_mark(":white_check_mark:") == Verdict("sign")
    assert verdict_from_mark("yes") == Verdict("sign")
    assert verdict_from_mark("❌") == Verdict("reject")
    assert verdict_from_mark("no") == Verdict("reject")
    assert verdict_from_mark("❤️") is None
    assert verdict_from_mark("maybe tomorrow") is None


def test_replies_bare_marks_rule_anything_else_is_an_edit():
    assert verdict_from_reply(" ✅ ") == Verdict("sign")
    assert verdict_from_reply("Actually say: on it, ETA 5pm") == Verdict("edit", "Actually say: on it, ETA 5pm")
    assert verdict_from_reply("   ") is None


def test_numbered_lines():
    p = parse_reply("1 ✅\n2 no\n3 tell them Tuesday works\n4")
    assert p.rulings == [
        (1, Verdict("sign")), (2, Verdict("reject")),
        (3, Verdict("edit", "tell them Tuesday works")), (4, Verdict("sign"))]
    assert not p.commands and not p.unparsed


def test_numbered_forms_with_punctuation_and_hash():
    p = parse_reply("#1: yes\n2. ❌\n3) 10am works for me")
    assert [n for n, _ in p.rulings] == [1, 2, 3]
    assert p.rulings[2][1] == Verdict("edit", "10am works for me")


def test_commands():
    assert parse_reply("all").commands == [("all", None)]
    assert parse_reply("Later").commands == [("later", None)]
    assert parse_reply("hold").commands == [("hold", None)]
    assert parse_reply("hold until 9").commands == [("hold", "9")]
    assert parse_reply("?").commands == [("digest", None)]


def test_unparsed_is_reported_not_guessed():
    p = parse_reply("what does this mean")
    assert p.empty and p.unparsed == ["what does this mean"]
