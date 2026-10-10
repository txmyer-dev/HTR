"""The draft's card: what a tap on a ruling notification opens.

A push can carry the draft and two buttons, not the reason for it. The card carries the rest:
who is waiting, their own words the draft rests on, the question the room could not answer,
and a box for the owner's words. Send and Skip here are the same signed rulings as the
buttons. Every link is relative, so the card works direct and behind the n8n front door."""
from __future__ import annotations

from html import escape

from ..proposals.store import PENDING, Proposal

_STYLE = ("body{font-family:system-ui;margin:0;padding:1.25rem;color:#1d222c;background:#f6f5f1;"
          "max-width:36rem}h1{font-size:1.3rem;margin:0}p.meta{color:#5d6270;margin:.25rem 0 1rem}"
          "h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:#5d6270;margin:1.25rem 0 .4rem}"
          "blockquote{margin:0 0 .5rem;padding:.6rem .8rem;border-left:3px solid #c9c8c1;background:#fff}"
          ".draft{padding:.8rem;background:#fff;border:1px solid #c9c8c1;border-radius:10px;white-space:pre-wrap}"
          ".gap{padding:.6rem .8rem;background:#fff6e0;border-radius:8px}"
          ".row{display:flex;gap:.5rem;margin-top:1rem}.row form{flex:1}"
          "button{width:100%;font:inherit;padding:.8rem;border-radius:999px;border:0;cursor:pointer}"
          ".send{background:#0064e0;color:#fff}.skip{background:#e8e7e1;color:#1d222c}"
          "textarea{width:100%;box-sizing:border-box;font:inherit;padding:.7rem;border-radius:10px;"
          "border:1px solid #c9c8c1;min-height:7rem}")


def render_card(p: Proposal, tokens: dict[str, str]) -> str:
    """`tokens` holds the signed `t` for sign, reject and edit on this one draft."""
    who = escape(p.to_name or p.to_number)
    meta = [escape(p.to_number)] if p.to_name else []
    meta.append(escape(p.kind))
    if p.deadline:
        meta.append(f"due {p.deadline:%a %b %d, %H:%M}")
    said = "".join(f"<blockquote>{escape(q)}</blockquote>" for q in p.basis)
    gap = (f"<h2>The room didn't know</h2><p class='gap'>{escape(p.gap)} "
           "Your words answer it, and the room remembers.</p>") if p.gap else ""
    if p.status == PENDING:
        act = (
            "<div class='row'>"
            f"<form method='post' action='sign?t={tokens['sign']}'><button class='send'>Send</button></form>"
            f"<form method='post' action='reject?t={tokens['reject']}'><button class='skip'>Skip</button></form>"
            "</div>"
            "<h2>Or send your own words</h2>"
            f"<form method='post' action='edit?t={tokens['edit']}'>"
            f"<textarea name='body'>{escape(p.body)}</textarea>"
            "<div class='row'><button class='send'>Send my words</button></div></form>")
    else:
        act = f"<p class='meta'>Already {escape(p.status)}.</p>"
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{who}</title><style>{_STYLE}</style></head><body>"
            f"<h1>{who}</h1><p class='meta'>{' · '.join(meta)}</p>"
            + (f"<h2>What they said</h2>{said}" if said else "")
            + gap
            + f"<h2>The draft</h2><div class='draft'>{escape(p.body)}</div>"
            + act + "</body></html>")
