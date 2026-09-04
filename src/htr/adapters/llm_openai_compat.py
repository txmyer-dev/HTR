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
    "Never invent facts, prices, or dates that are not in the input. If the request needs the "
    "owner's decision, say the owner will confirm. "
    "Return JSON: {\"body\": string, \"basis\": [verbatim quotes from the input the reply rests on]}."
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

    def draft_reply(self, req: DraftRequest) -> Draft:
        if not (self.base and self.model):
            raise RuntimeError("LLM gateway not configured (HTR_LLM_BASE_URL, HTR_LLM_MODEL)")
        user = json.dumps({
            "business": req.business_name, "owner": req.owner_name,
            "counterparty": req.counterparty_name or req.counterparty_number,
            "channel": req.channel, "their_message": req.their_message,
            "prior": list(req.prior), "deadline_hint": req.deadline_hint,
        })
        r = httpx.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
            json={"model": self.model, "temperature": 0.3, "stream": False,
                  "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                  "response_format": {"type": "json_object"}},
            timeout=90)
        r.raise_for_status()
        content = _content_of(r)
        data = _extract_json(content)
        body = str(data.get("body", "")).strip() if data else content.strip().strip("`").strip()
        if not body:
            raise RuntimeError("model returned an empty draft")
        source = req.their_message + "\n" + "\n".join(req.prior)
        basis = tuple(q for q in (str(x).strip() for x in (data or {}).get("basis", [])) if q and q in source)
        return Draft(body=body[:600], basis=basis or (req.their_message.strip(),))
