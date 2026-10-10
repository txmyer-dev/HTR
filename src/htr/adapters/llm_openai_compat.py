"""An OpenAI-compatible chat-completions drafter: Omniroute, or any gateway that speaks that
shape. Vertex/Gemini gets its own adapter in release 2 (STEALS: icm-agent src/vertex.mjs).

Env: HTR_LLM_BASE_URL (e.g. https://omniroute.example/v1), HTR_LLM_API_KEY, HTR_LLM_MODEL.
The model is asked for JSON with `body` and `basis`; basis must be verbatim quotes, and the
adapter drops any quote that is not actually in the source text, so the owner never rules on
evidence that was made up."""
from __future__ import annotations

import json
import os
import re

import httpx

from ..ports.llm import Draft, DraftRequest

SYSTEM = (
    "You draft one reply that a business owner will send as themselves. "
    "Plain, warm, specific. For SMS and calls: under 300 characters. For email: a short email, "
    "no subject line, a greeting and a sign-off with the owner's first name, under 900 characters. "
    "Never invent facts, prices, or dates that are not in the input, the facts you were given, or "
    "the owner's past corrections. If the request needs something you do not know — not in the "
    "message, not in the facts, not in a past correction — say the owner will confirm, and put the "
    "specific missing thing (a short phrase, e.g. \"Saturday opening hours\") in `gap`; otherwise "
    "`gap` is null. If the owner's past corrections show a way of answering, match it over your own. "
    "Return JSON: {\"body\": string, \"basis\": [verbatim quotes from the input the reply rests on], "
    "\"gap\": string or null}."
)

DISTILL = (
    "You turn a business owner's reply into what it tells you about the business. "
    "Return JSON: {\"statement\": string}: one or two plain sentences, third person, present tense, "
    "facts only (hours, prices, policies, places, how to book). No greeting, no names of people, "
    "no sign-off, no pleasantries. If the reply states no fact about the business, "
    "return {\"statement\": null}."
)


def _content_of(r: httpx.Response) -> str:
    """A plain completion, or, for a gateway that streams regardless of `stream: false`,
    the concatenated content deltas of an SSE body (reasoning deltas are not content)."""
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" not in ctype:
        return r.json()["choices"][0]["message"]["content"] or ""
    parts: list[str] = []
    for line in r.text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            piece = delta.get("content") or (choice.get("message") or {}).get("content")
            if piece:
                parts.append(piece)
    return "".join(parts)


def _extract_json(content: str) -> dict | None:
    """Models wrap JSON in fences or prose despite response_format. Take the first {...}
    that parses; None means the reply was plain text and is used as the body verbatim."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    for cand in (text, *re.findall(r"\{.*\}", text, flags=re.DOTALL)):
        try:
            obj = json.loads(cand, strict=False)          # models put raw newlines inside strings
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


class OpenAICompatLLM:
    def __init__(self) -> None:
        self.base = os.environ.get("HTR_LLM_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("HTR_LLM_API_KEY", "")
        self.model = os.environ.get("HTR_LLM_MODEL", "")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _chat(self, system: str, user: dict) -> str:
        r = httpx.post(
            f"{self.base}/chat/completions",
            headers=self._headers(),
            json={"model": self.model, "temperature": 0.3, "stream": False,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user)}],
                  "response_format": {"type": "json_object"}},
            timeout=90)
        r.raise_for_status()
        return _content_of(r)

    def draft_reply(self, req: DraftRequest) -> Draft:
        if not (self.base and self.model):
            raise RuntimeError("LLM gateway not configured (HTR_LLM_BASE_URL, HTR_LLM_MODEL)")
        content = self._chat(SYSTEM, {
            "business": req.business_name, "owner": req.owner_name,
            "counterparty": req.counterparty_name or req.counterparty_number,
            "channel": req.channel, "their_message": req.their_message,
            "prior": list(req.prior), "deadline_hint": req.deadline_hint,
            "facts_about_the_business": list(req.facts),
            "your_past_corrections": [{"they_said": l.their_message, "you_drafted": l.draft, "owner_sent": l.sent}
                                      for l in req.lessons],
        })
        data = _extract_json(content)
        body = str(data.get("body", "")).strip() if data else content.strip().strip("`").strip()
        if not body:
            raise RuntimeError("model returned an empty draft")
        source = req.their_message + "\n" + "\n".join(req.prior)
        basis = tuple(q for q in (str(x).strip() for x in (data or {}).get("basis", [])) if q and q in source)
        gap = str(data.get("gap") or "").strip()[:200] or None if data else None
        return Draft(body=body[:600], basis=basis or (req.their_message.strip(),), gap=gap)

    def distill_fact(self, question: str | None, answer: str) -> str | None:
        if not (self.base and self.model):
            return None
        try:
            content = self._chat(DISTILL, {"asked": question, "owner_replied": answer})
        except Exception:                                  # noqa: BLE001 — a failure leaves the owner's words standing
            return None
        data = _extract_json(content)
        s = data.get("statement") if data else None
        if not isinstance(s, str):
            return None
        s = " ".join(s.split())
        return s[:600] if s and s.lower() not in ("null", "none") else None
