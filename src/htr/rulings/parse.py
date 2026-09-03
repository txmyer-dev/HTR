"""Reading a ruling off an SMS (STEALS: waggle src/rulings/parse.ts, ported, plus the numbered
form). Deliberately narrow: a mark signs or rejects, anything else is the words to send
instead. A digest numbers the drafts, so each line of a reply starts with the number it rules on.

    1            sign draft 1
    1 ✅ / yes   sign draft 1
    2 no / ❌    reject draft 2
    3 tell them Tuesday works      replace draft 3's body with those words, then sign
    all          sign every draft in the digest
    later        leave everything pending until the next digest
    hold [until 9]   hold the room
    ?            send the digest now
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SIGN_MARKS = {"✅", "✔", "☑", "👍", "+", ":white_check_mark:", ":heavy_check_mark:", ":ballot_box_with_check:"}
REJECT_MARKS = {"❌", "✖", "🚫", "⛔", "👎", "-", ":x:", ":heavy_multiplication_x:", ":no_entry_sign:", ":no_entry:"}
SIGN_WORDS = {"yes", "y", "ok", "okay", "send", "send it", "go", "approve", "approved", "sign", "yep", "yup", "sure"}
REJECT_WORDS = {"no", "n", "skip", "nope", "reject", "drop", "don't", "dont", "no thanks", "cancel"}

_VARIATION = re.compile("[︎️]")
_NUMBERED = re.compile(r"^#?(\d{1,3})\s*[.:)\-–]?\s*(.*)$", re.DOTALL)
_HOLD = re.compile(r"^hold(?:\s+(?:until|till|til|to)\s+(.+))?$", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    kind: str                     # sign | reject | edit
    body: str | None = None       # the replacement text, for edit


@dataclass
class ParsedReply:
    rulings: list[tuple[int, Verdict]] = field(default_factory=list)
    commands: list[tuple[str, str | None]] = field(default_factory=list)   # (all|later|hold|digest, arg)
    unparsed: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.rulings and not self.commands


def normalize(text: str) -> str:
    """Strip variation selectors and whitespace so "✔️" and "✔" agree."""
    return _VARIATION.sub("", text).strip()


def verdict_from_mark(text: str) -> Verdict | None:
    c = normalize(text)
    low = c.lower().rstrip(".!")
    if c in SIGN_MARKS or low in SIGN_WORDS:
        return Verdict("sign")
    if c in REJECT_MARKS or low in REJECT_WORDS:
        return Verdict("reject")
    return None


def verdict_from_reply(text: str) -> Verdict | None:
    """A bare mark means what the mark means; any other text is "send this instead"."""
    as_mark = verdict_from_mark(text)
    if as_mark:
        return as_mark
    t = text.strip()
    return Verdict("edit", t) if t else None


def parse_reply(text: str) -> ParsedReply:
    out = ParsedReply()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = normalize(line).lower().rstrip(".!")
        m = _NUMBERED.match(line)
        if m:
            n = int(m.group(1))
            rest = m.group(2).strip()
            verdict = verdict_from_reply(rest) if rest else Verdict("sign")
            if verdict:
                out.rulings.append((n, verdict))
            continue
        if low in {"all", "yes all", "send all", "all yes", "✅ all", "all ✅"}:
            out.commands.append(("all", None))
        elif low in {"later", "not now", "tomorrow"}:
            out.commands.append(("later", None))
        elif _HOLD.match(low):
            out.commands.append(("hold", (_HOLD.match(low).group(1) or "").strip() or None))
        elif low in {"?", "digest", "status", "what's waiting", "whats waiting", "list"}:
            out.commands.append(("digest", None))
        else:
            out.unparsed.append(line)
    return out
