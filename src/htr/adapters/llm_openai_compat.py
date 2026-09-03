"""An OpenAI-compatible chat-completions drafter: Omniroute, or any gateway that speaks that
shape. Vertex/Gemini gets its own adapter in release 2 (STEALS: icm-agent src/vertex.mjs).

Env: HTR_LLM_BASE_URL (e.g. https://omniroute.example/v1), HTR_LLM_API_KEY, HTR_LLM_MODEL.
The model is asked for JSON with `body` and `basis`; basis must be verbatim quotes, and the
adapter drops any quote that is not actually in the source text, so the owner never rules on
evidence that was made up."""
from __future__ import annotations

import json
import os

import httpx

from ..ports.llm import Draft, DraftRequest

SYSTEM = (
    "You draft one short reply that a business owner will send as themselves, by SMS. "
    "Plain, warm, specific, under 300 characters. Never invent facts, prices, or dates that are "
    "not in the input. If the request needs the owner's decision, say the owner will confirm. "
    "Return JSON: {\"body\": string, \"basis\": [verbatim quotes from the input the reply rests on]}."
)


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
            json={"model": self.model, "temperature": 0.3,
                  "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                  "response_format": {"type": "json_object"}},
            timeout=30)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        body = str(data.get("body", "")).strip()
        if not body:
            raise RuntimeError("model returned an empty draft")
        source = req.their_message + "\n" + "\n".join(req.prior)
        basis = tuple(q for q in (str(x).strip() for x in data.get("basis", [])) if q and q in source)
        return Draft(body=body[:600], basis=basis or (req.their_message.strip(),))
