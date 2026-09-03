"""The numbered digest: what waits, as one SMS the owner can rule on line by line.

    3 waiting on you.

    1 Marco (call, Fri): "Do you have a leather bag under $200?" → Yes, the Harlow, $185...
    2 +1 555 0142 (sms): "Are you open Saturday?" → Hi there, thanks for your message...

    Reply with the number and ✅, no, or better words. "all" sends everything.

One message, not one per draft: Twilio concatenates up to 1600 characters, and a single
message keeps the numbering together on the owner's screen. Past the cap the tail is counted,
not dropped silently.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo

from ..proposals.store import Proposal

HELP_LINE = "Reply with the number and ✅, no, or better words. \"all\" sends everything."
MAX_CHARS = 1500


def _one_line(s: str, n: int) -> str:
    t = " ".join(s.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _who(p: Proposal) -> str:
    return p.to_name or p.to_number


def _when(p: Proposal, tz: ZoneInfo, now: datetime) -> str:
    if not p.deadline:
        return ""
    d = p.deadline.astimezone(tz)
    if d.date() == now.astimezone(tz).date():
        return ", today"
    if (d.date() - now.astimezone(tz).date()).days < 7:
        return ", " + d.strftime("%a")
    return ", " + d.strftime("%b %-d") if hasattr(d, "strftime") else ""


def render_line(n: int, p: Proposal, tz: ZoneInfo, now: datetime) -> str:
    channel = "call" if (p.source_kind or "").startswith("call") else "sms"
    ask = _one_line(p.basis[0], 80) if p.basis else ""
    ask_part = f' "{ask}"' if ask else ""
    return f"{n} {_who(p)} ({channel}{_when(p, tz, now)}):{ask_part} → {_one_line(p.body, 160)}"


def render_digest(items: Sequence[tuple[int, Proposal]], *, timezone: str = "UTC",
                  now: datetime | None = None) -> str:
    """items: (digest number, proposal), already numbered by the caller."""
    tz = ZoneInfo(timezone)
    now = now or datetime.now(tz)
    if not items:
        return "Nothing waiting on you."
    head = f"{len(items)} waiting on you." if len(items) > 1 else "1 waiting on you."
    lines = [head, ""]
    budget = MAX_CHARS - len(head) - len(HELP_LINE) - 4
    shown = 0
    for n, p in items:
        line = render_line(n, p, tz, now)
        if budget - len(line) - 1 < 0 and shown > 0:
            break
        lines.append(line)
        budget -= len(line) + 1
        shown += 1
    if shown < len(items):
        lines.append(f"…and {len(items) - shown} more; reply ? after ruling to see them.")
    lines += ["", HELP_LINE]
    return "\n".join(lines)


def segments(text: str, limit: int = 160) -> list[str]:
    """Split on line boundaries so numbering survives a carrier that will not concatenate.
    A single line longer than the limit is hard-split."""
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        candidate = line if not cur else cur + "\n" + line
        if len(candidate) > limit:
            out.append(cur)
            cur = line
        else:
            cur = candidate
    if cur.strip():
        out.append(cur)
    return [s.strip("\n") for s in out if s.strip()]
