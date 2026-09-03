"""A cheap deadline reader. If the message names a day, the draft carries that deadline and
the digest shows it; otherwise the owner's default applies. Deliberately small: it finds
"Friday", "tomorrow", "today", "by Thursday". Anything subtler is the owner's call."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(day)?\b", re.IGNORECASE)
_REL = re.compile(r"\b(today|tomorrow|tonight|asap|end of day|eod)\b", re.IGNORECASE)


def find_deadline(text: str, now: datetime, timezone: str = "UTC") -> tuple[datetime | None, str | None]:
    """Returns (deadline in UTC at end of that local day, the hint as the sender said it)."""
    tz = ZoneInfo(timezone)
    local = now.astimezone(tz)
    m = _REL.search(text)
    if m:
        word = m.group(1).lower()
        days = 1 if word == "tomorrow" else 0
        target = (local + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
        return target.astimezone(ZoneInfo("UTC")), m.group(1)
    m = _DAY.search(text)
    if m:
        stem = m.group(1).lower()[:3]
        idx = next(i for i, d in enumerate(DAYS) if d.startswith(stem))
        ahead = (idx - local.weekday()) % 7
        if ahead == 0:
            ahead = 7                                  # "Friday" said on a Friday means next week
        target = (local + timedelta(days=ahead)).replace(hour=17, minute=0, second=0, microsecond=0)
        return target.astimezone(ZoneInfo("UTC")), DAYS[idx].capitalize()
    return None, None
