"""The first reader (STEALS: waggle src/tools/waiting.ts, ported).

"What's waiting on me?" is a pure heuristic over recent messages, so the loop can ask one
question and get the list instead of reading every thread and guessing.

A message is waiting on the owner when it is addressed to them (mentions a name), replies to
something they wrote, or asks a question, and the owner has not answered it since. Cheap on
purpose: it is a starting list for drafts the owner will rule on, not a verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

QUESTION_OPENERS = re.compile(
    r"^(does|do|did|can|could|would|will|should|is|are|was|were|who|what|when|where|why|how|any(one|body))\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Msg:
    id: str
    sender: str
    content: str
    created_at: float
    reply_to: str | None = None
    root_id: str | None = None


@dataclass(frozen=True)
class Thread:
    channel_id: str
    messages: tuple[Msg, ...]
    channel_name: str | None = None


@dataclass(frozen=True)
class Me:
    sender_ids: frozenset[str]
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class WaitingItem:
    msg: Msg
    channel_id: str
    channel_name: str | None
    reason: str                   # mention | reply-to-you | question


def is_question(text: str) -> bool:
    return "?" in text or bool(QUESTION_OPENERS.match(text.strip()))


def waiting_on_me(threads: Iterable[Thread], me: Me, *, since: float | None = None,
                  limit: int = 20) -> list[WaitingItem]:
    names = [n.strip().lower() for n in me.names if len(n.strip()) >= 3]
    out: list[WaitingItem] = []
    for th in threads:
        ordered = sorted(th.messages, key=lambda m: m.created_at)
        mine = [m for m in ordered if m.sender in me.sender_ids]
        my_ids = {m.id for m in mine}
        for m in ordered:
            if m.sender in me.sender_ids:
                continue
            if since is not None and m.created_at < since:
                continue
            text = m.content.lower()
            reason: str | None = None
            if any(n in text for n in names):
                reason = "mention"
            elif (m.reply_to and m.reply_to in my_ids) or (m.root_id and m.root_id in my_ids):
                reason = "reply-to-you"
            elif is_question(m.content):
                reason = "question"
            if reason is None:
                continue
            thread_root = m.root_id or m.id
            answered = any(
                r.created_at > m.created_at
                and (r.reply_to == m.id or r.root_id == thread_root or r.root_id == m.id)
                for r in mine)
            if answered:
                continue
            out.append(WaitingItem(msg=m, channel_id=th.channel_id, channel_name=th.channel_name, reason=reason))
    out.sort(key=lambda w: w.msg.created_at, reverse=True)
    return out[:limit]


def latest_unanswered(inbound: list[Msg], outbound: list[Msg]) -> Msg | None:
    """The SMS case: one counterparty, one thread, no reply ids. The newest inbound message
    is waiting if nothing went out after it. Every text from a counterparty is treated as
    addressed to the owner; the question heuristic is not required on a direct channel."""
    if not inbound:
        return None
    newest = max(inbound, key=lambda m: m.created_at)
    if any(o.created_at > newest.created_at for o in outbound):
        return None
    return newest
