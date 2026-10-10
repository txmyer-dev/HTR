"""The second brain: a draft that cannot answer names the gap; the owner's edit is the answer
and the lesson at once; a bare "remember ..." teaches without waiting to be asked (STEALS:
htr-convex convex/facts.ts, lib/drafter/lessons.ts)."""
from __future__ import annotations

import json

from htr.facts.lessons import to_lessons
from htr.facts.store import FactStore, fact_text
from htr.jobs import drain
from htr.ports.llm import Draft, DraftRequest, Lesson
from htr.rulings.parse import parse_reply

from conftest import OWNER, sms_form, texts_to

CUSTOMER = "+15550004444"


# ---- FactStore ------------------------------------------------------------------------------

def test_learn_dedupes_same_question_and_answer(engine):
    store = FactStore(engine)
    a = store.learn("tony", "Yes, Saturdays 9 to 2", question="Saturday hours")
    b = store.learn("tony", "Yes, Saturdays 9 to 2", question="Saturday hours")   # replayed ruling
    c = store.learn("tony", "Yes, Saturdays 9 to 2", question="Sunday hours")     # different question: new
    assert a is not None and b is None and c is not None
    assert len(store.recent("tony")) == 2


def test_learn_ignores_empty_and_squashes_whitespace(engine):
    store = FactStore(engine)
    assert store.learn("tony", "   ") is None
    f = store.learn("tony", "  Dogs   welcome  \n here  ")
    assert f.answer == "Dogs welcome here"


def test_fact_text_prefers_the_distilled_statement(engine):
    store = FactStore(engine)
    f = store.learn("tony", "yeah dogs are fine, there's a yard out back")
    assert fact_text(f) == f.answer
    store.set_statement(f.id, "Dogs are welcome; there is a yard.")
    assert fact_text(store.get(f.id)) == "Dogs are welcome; there is a yard."


# ---- lessons ----------------------------------------------------------------------------------

def test_to_lessons_drops_empty_and_unchanged_pairs_and_caps_at_max():
    rows = [
        {"their_message": "m1", "draft": "a", "sent": "a"},          # unchanged: not a lesson
        {"their_message": "m2", "draft": "", "sent": "b"},            # empty draft: not a lesson
        {"their_message": "m3", "draft": "c", "sent": ""},            # empty sent: not a lesson
        *[{"their_message": f"m{i}", "draft": f"d{i}", "sent": f"s{i}"} for i in range(4, 12)],
    ]
    out = to_lessons(rows, max_=5)
    assert len(out) == 5
    assert out[0] == Lesson(their_message="m4", draft="d4", sent="s4")


def test_to_lessons_clips_long_text():
    rows = [{"their_message": "x" * 900, "draft": "d", "sent": "s" * 900}]
    (lesson,) = to_lessons(rows)
    assert len(lesson.sent) == 500 and lesson.sent.endswith("…")


# ---- parsing "remember" ------------------------------------------------------------------------

def test_parse_reply_recognizes_a_bare_remember():
    parsed = parse_reply("remember parking is free after 6")
    assert parsed.commands == [("remember", "parking is free after 6")]
    assert not parsed.rulings and not parsed.unparsed


# ---- digest line ---------------------------------------------------------------------------

def test_digest_line_shows_the_gap(engine, registry, store):
    p = store.propose("tony", CUSTOMER, "Let me check on that.", basis=("Are you open Saturday?",),
                      gap="Saturday opening hours")
    from htr.digest.render import render_line
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    line = render_line(1, p, ZoneInfo("America/Chicago"), datetime.now(timezone.utc))
    assert "you haven't told me: Saturday opening hours" in line


# ---- the template adapter is honest about not distilling ----------------------------------

def test_template_llm_never_distills():
    from htr.adapters.llm_template import TemplateLLM
    assert TemplateLLM().distill_fact("Saturday hours?", "9 to 2") is None


# ---- the openai-compatible adapter: facts/lessons in, gap and statement out -----------------

def _fake_response(body: dict):
    class R:
        status_code = 200
        headers = {"content-type": "application/json"}
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps(body)}}]}
    return R()


def test_openai_compat_sends_facts_and_lessons_and_parses_gap(monkeypatch):
    from htr.adapters.llm_openai_compat import OpenAICompatLLM
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["json"] = json
        return _fake_response({"body": "Let me check on that.", "basis": ["Are you open Saturday?"],
                               "gap": "Saturday opening hours"})

    monkeypatch.setattr("htr.adapters.llm_openai_compat.httpx.post", fake_post)
    monkeypatch.setenv("HTR_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("HTR_LLM_MODEL", "m")
    llm = OpenAICompatLLM()
    req = DraftRequest(business_name="B", owner_name="Tony", counterparty_number=CUSTOMER,
                       counterparty_name=None, channel="sms", their_message="Are you open Saturday?",
                       facts=("Dogs are welcome.",), lessons=(Lesson("m", "draft", "sent"),))
    draft = llm.draft_reply(req)
    assert draft.gap == "Saturday opening hours"
    sent_user = json.loads(seen["json"]["messages"][1]["content"])
    assert sent_user["facts_about_the_business"] == ["Dogs are welcome."]
    assert sent_user["your_past_corrections"] == [{"they_said": "m", "you_drafted": "draft", "owner_sent": "sent"}]


def test_openai_compat_gap_is_null_when_answerable(monkeypatch):
    from htr.adapters.llm_openai_compat import OpenAICompatLLM
    monkeypatch.setattr("htr.adapters.llm_openai_compat.httpx.post",
                        lambda *a, **k: _fake_response({"body": "Yes!", "basis": ["Are you open Saturday?"], "gap": None}))
    monkeypatch.setenv("HTR_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("HTR_LLM_MODEL", "m")
    req = DraftRequest(business_name="B", owner_name="Tony", counterparty_number=CUSTOMER,
                       counterparty_name=None, channel="sms", their_message="Are you open Saturday?")
    assert OpenAICompatLLM().draft_reply(req).gap is None


def test_openai_compat_distill_fact_parses_statement_and_null(monkeypatch):
    from htr.adapters.llm_openai_compat import OpenAICompatLLM
    monkeypatch.setenv("HTR_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("HTR_LLM_MODEL", "m")
    llm = OpenAICompatLLM()

    monkeypatch.setattr("htr.adapters.llm_openai_compat.httpx.post",
                        lambda *a, **k: _fake_response({"statement": "Dogs are welcome; there is a yard."}))
    assert llm.distill_fact("Are dogs allowed?", "yeah, there's a yard out back") == "Dogs are welcome; there is a yard."

    monkeypatch.setattr("htr.adapters.llm_openai_compat.httpx.post",
                        lambda *a, **k: _fake_response({"statement": None}))
    assert llm.distill_fact(None, "thanks!") is None


def test_openai_compat_distill_fact_returns_none_on_failure(monkeypatch):
    from htr.adapters.llm_openai_compat import OpenAICompatLLM
    monkeypatch.setenv("HTR_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("HTR_LLM_MODEL", "m")

    def boom(*a, **k):
        raise RuntimeError("network")

    monkeypatch.setattr("htr.adapters.llm_openai_compat.httpx.post", boom)
    assert OpenAICompatLLM().distill_fact("q", "a") is None


# ---- end to end: gap -> edit -> fact -> distilled -> next draft sees it --------------------

class _GapThenFactLLM:
    """Names a gap once; once a fact exists, answers from it instead. Not a model: a spy."""
    def __init__(self):
        self.requests: list[DraftRequest] = []

    def draft_reply(self, req: DraftRequest) -> Draft:
        self.requests.append(req)
        if req.facts:
            return Draft(body=f"Yes! {req.facts[0]}", basis=(req.their_message,))
        return Draft(body="Let me check on that and get back to you.", basis=(req.their_message,),
                     gap="Saturday opening hours")

    def distill_fact(self, question, answer):
        return f"Open Saturdays: {answer}"


def test_gap_names_then_edit_teaches_then_next_draft_knows(client, engine, registry, store, monkeypatch):
    fake = _GapThenFactLLM()
    monkeypatch.setattr("htr.adapters.llm_for", lambda tenant: fake)

    client.post("/webhooks/tony/sms", data=sms_form(CUSTOMER, "Are you open Saturday?", "SM-1"))
    drain(engine, registry)
    (p,) = store.pending("tony")
    assert p.gap == "Saturday opening hours"
    assert "you haven't told me: Saturday opening hours" in texts_to(OWNER)[-1]

    # the owner's edit answers the gap: the ruling is the lesson and the answer at once
    client.post("/webhooks/tony/sms", data=sms_form(OWNER, "1 Yes, Saturdays 9 to 2"))
    drain(engine, registry)
    fact_store = FactStore(engine)
    (fact,) = fact_store.recent("tony")
    assert fact.question == "Saturday opening hours" and fact.answer == "Yes, Saturdays 9 to 2"
    assert fact.statement == "Open Saturdays: Yes, Saturdays 9 to 2"     # distill_fact already ran

    # a later, similar question sees the fact in front of the drafter
    client.post("/webhooks/tony/sms", data=sms_form(CUSTOMER, "Are you open this Saturday too?", "SM-2"))
    drain(engine, registry)
    (live,) = store.pending("tony")
    assert live.body == "Yes! Open Saturdays: Yes, Saturdays 9 to 2"
    assert fake.requests[-1].facts == ("Open Saturdays: Yes, Saturdays 9 to 2",)


def test_bare_remember_teaches_without_a_gap(client, engine, registry, store, monkeypatch):
    fake = _GapThenFactLLM()
    monkeypatch.setattr("htr.adapters.llm_for", lambda tenant: fake)
    r = client.post("/webhooks/tony/sms", data=sms_form(OWNER, "remember parking is free after 6"))
    drain(engine, registry)
    assert "remember" in texts_to(OWNER)[-1].lower()
    (fact,) = FactStore(engine).recent("tony")
    assert fact.question is None and fact.answer == "parking is free after 6"
