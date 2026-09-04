"""Retell proves itself two ways; a stranger proves nothing."""
from __future__ import annotations

import hashlib
import hmac

from htr.adapters.voice_retell import RetellVoice


def _sig(key: str, body: bytes, ts: int) -> str:
    return f"v={ts},d={hmac.new(key.encode(), body + str(ts).encode(), hashlib.sha256).hexdigest()}"


def test_shared_secret_header_is_accepted():
    v = RetellVoice(api_key="")
    assert v.verify("s3cret", {"x-retell-secret": "s3cret"}, b"{}")
    assert not v.verify("s3cret", {"x-retell-secret": "nope"}, b"{}")
    assert not v.verify("s3cret", {}, b"{}")


def test_signature_over_raw_body_within_window():
    v = RetellVoice(api_key="key_abc")
    body = b'{"event":"call_ended","call":{"call_id":"c1"}}'
    ts = 1_700_000_000_000
    good = _sig("key_abc", body, ts)
    assert v.verify_signature(good, body, now_ms=ts + 1000)
    assert not v.verify_signature(good, body + b" ", now_ms=ts + 1000), "raw body must match byte for byte"
    assert not v.verify_signature(good, body, now_ms=ts + 6 * 60 * 1000), "outside the five-minute window"
    assert not v.verify_signature(_sig("other", body, ts), body, now_ms=ts)
    assert not v.verify_signature("garbage", body, now_ms=ts)


def test_verify_prefers_secret_then_signature():
    v = RetellVoice(api_key="key_abc")
    body = b"{}"; ts = 1_700_000_000_000
    headers = {"X-Retell-Signature": _sig("key_abc", body, ts)}
    import htr.adapters.voice_retell as mod
    real = mod.time.time
    mod.time.time = lambda: ts / 1000
    try:
        assert v.verify("s3cret", headers, body), "signature alone is enough"
        assert not v.verify("s3cret", {"x-retell-signature": "v=1,d=00"}, body)
    finally:
        mod.time.time = real


def test_call_ended_without_proof_is_refused_unless_retell_confirms(client, engine, tmp_path, monkeypatch):
    """The secret-locked tenant from the stolen suite: an unsigned call-ended is refused, unless
    Retell's API confirms the call exists (the backstop)."""
    from fastapi.testclient import TestClient
    from htr.config import TenantRegistry
    from htr.web.app import create_app
    from htr.adapters import voice_retell
    monkeypatch.setenv("SB_TEST_SECRET", "s3cret")
    monkeypatch.setenv("RETELL_API_KEY", "key_abc")
    f = tmp_path / "t.yaml"
    f.write_text("""
tenants:
  locked:
    display_name: "Locked"
    numbers: ["+15550000003"]
    owner_phone: "+15550000029"
    adapters: { voice: retell, messaging: console }
    secrets: { voice_webhook_secret: SB_TEST_SECRET }
""", encoding="utf-8")
    c = TestClient(create_app(engine, TenantRegistry.load(f)))
    payload = {"event": "call_ended", "call": {"call_id": "real-1", "from_number": "+1", "to_number": "+15550000003", "transcript": "hi"}}
    monkeypatch.setattr(voice_retell.RetellVoice, "confirm_call", lambda self, cid, f="", t="": False)
    assert c.post("/webhooks/locked/call-ended", json=payload).status_code == 401
    monkeypatch.setattr(voice_retell.RetellVoice, "confirm_call", lambda self, cid, f="", t="": cid == "real-1")
    assert c.post("/webhooks/locked/call-ended", json=payload).status_code == 200
    assert c.post("/webhooks/locked/call-ended", json={"event": "call_analyzed", "call": payload["call"]}).json().get("ignored") == "call_analyzed"
