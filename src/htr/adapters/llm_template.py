"""The no-model drafter. Deterministic, offline, honest: it acknowledges, quotes, and promises a
reply by the deadline. Client zero can run on this alone; every test does. Swap the adapter in
install.yaml when a model account exists."""
from __future__ import annotations

from ..ports.llm import Draft, DraftRequest


class TemplateLLM:
    def draft_reply(self, req: DraftRequest) -> Draft:
        who = req.counterparty_name or "there"
        ask = " ".join(req.their_message.split())
        quote = ask if len(ask) <= 90 else ask[:89].rstrip() + "…"
        when = f" by {req.deadline_hint}" if req.deadline_hint else " shortly"
        via = "calling" if req.channel == "call" else "your message"
        body = (f"Hi {who}, thanks for {via}. Got it: \"{quote}\". "
                f"{req.owner_name} will get back to you{when}.")
        return Draft(body=body, basis=(req.their_message.strip(),))
