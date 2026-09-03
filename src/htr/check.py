"""Probe an install's accounts, one at a time, read-only where possible.

    python -m htr.check [tenant] [--only retell,llm,ntfy,twilio]

retell  GET /list-agents with the API key; renders the provisioning payload (no create).
llm     one real draft through the configured LLM adapter.
ntfy    one push to the topic (this one is a write: you should see it on your phone).
twilio  fetches the account; sends nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

from . import adapters
from .config import TenantRegistry
from .env import load_dotenv
from .ports.llm import DraftRequest

RETELL_BASE = "https://api.retellai.com"


def _ok(name: str, detail: str) -> None:
    print(f"  ok   {name:<8} {detail}")


def _bad(name: str, detail: str) -> None:
    print(f"  FAIL {name:<8} {detail}")


def check_retell(tenant) -> bool:
    key = os.environ.get("RETELL_API_KEY", "")
    if not key:
        _bad("retell", "RETELL_API_KEY is empty"); return False
    try:
        r = httpx.get(f"{RETELL_BASE}/list-agents", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError as exc:
        _bad("retell", f"request failed: {exc!r}"); return False
    if r.status_code != 200:
        _bad("retell", f"list-agents -> HTTP {r.status_code}: {r.text[:120]}"); return False
    agents = r.json() if isinstance(r.json(), list) else r.json().get("agents", [])
    names = [a.get("agent_name") for a in agents][:5]
    _ok("retell", f"key accepted; {len(agents)} agent(s) on the account {names}")
    from .provision import build_tools, llm_payload
    base = os.environ.get("HTR_PUBLIC_URL", "").rstrip("/")
    if not base:
        _bad("retell", "HTR_PUBLIC_URL is empty; tool URLs cannot be built"); return False
    tools = build_tools(tenant, base)
    payload = llm_payload(tenant, base)
    _ok("retell", f"provisioning renders: {len(tools)} tools -> {base}/webhooks/{tenant.id}/tool; "
                  f"prompt {len(payload['general_prompt'])} chars")
    if not tenant.secrets.get("voice_webhook_secret"):
        _bad("retell", "voice_webhook_secret resolves empty: HTR_TONY_RETELL_SECRET not set"); return False
    _ok("retell", "webhook secret present")
    return True


def check_llm(tenant) -> bool:
    kind = tenant.adapters.get("llm", "template")
    try:
        llm = adapters.llm_for(tenant)
    except KeyError as exc:
        _bad("llm", str(exc)); return False
    req = DraftRequest(business_name=tenant.display_name, owner_name=tenant.owner.name,
                       counterparty_number="+15550001234", counterparty_name="Marco", channel="call",
                       their_message="Do you have a leather bag under $200? I need it by Friday.",
                       deadline_hint="Friday")
    t0 = time.monotonic()
    try:
        d = llm.draft_reply(req)
    except Exception as exc:                                    # noqa: BLE001
        _bad("llm", f"{kind}: {exc!r}"); return False
    ms = int((time.monotonic() - t0) * 1000)
    _ok("llm", f"{kind} ({os.environ.get('HTR_LLM_MODEL', '-')}) in {ms} ms")
    print(f"         draft: {d.body}")
    print(f"         basis: {list(d.basis)}")
    return True


def check_ntfy(tenant) -> bool:
    topic = os.environ.get("HTR_NTFY_TOPIC", "")
    url = os.environ.get("HTR_NTFY_URL", "https://ntfy.sh").rstrip("/")
    if not topic:
        _bad("ntfy", "HTR_NTFY_TOPIC is empty"); return False
    try:
        r = httpx.post(f"{url}/{topic}", content=f"[htr] {tenant.display_name}: check at {time.strftime('%H:%M:%S')}".encode(),
                       headers={"Title": "Hold the Room", "Priority": "default"}, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        _bad("ntfy", f"{url}/{topic}: {exc!r}"); return False
    _ok("ntfy", f"pushed to {url}/{topic}; check the phone")
    return True


def check_twilio(tenant) -> bool:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    if not sid:
        _bad("twilio", "TWILIO_ACCOUNT_SID is empty (skipped by choice?)"); return False
    from .adapters.messaging_twilio import TwilioMessaging
    tw = TwilioMessaging(tenant.secrets)
    try:
        r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json", auth=tw._auth(), timeout=15)
    except httpx.HTTPError as exc:
        _bad("twilio", f"{exc!r}"); return False
    if r.status_code != 200:
        _bad("twilio", f"HTTP {r.status_code}"); return False
    _ok("twilio", f"account {r.json().get('friendly_name')} ({r.json().get('status')}); from {tw.from_number or '(no from number)'}")
    return True


CHECKS = {"retell": check_retell, "llm": check_llm, "ntfy": check_ntfy, "twilio": check_twilio}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tenant", nargs="?", default=None)
    ap.add_argument("--only", default="retell,llm,ntfy,twilio")
    args = ap.parse_args(argv)
    loaded = load_dotenv()
    registry = TenantRegistry.load()
    tenant = registry.get(args.tenant) if args.tenant else registry.all()[0]
    print(f"install: {tenant.id} ({tenant.display_name}) | owner {tenant.owner.phone} | "
          f"adapters {json.dumps({k: v for k, v in tenant.adapters.items() if k in ('messaging', 'voice', 'llm')})}")
    print(f".env: {len(loaded)} keys loaded | db {os.environ.get('HTR_DATABASE_URL', 'sqlite:///htr.db')}")
    results = {}
    for name in [n.strip() for n in args.only.split(",") if n.strip()]:
        fn = CHECKS.get(name)
        if fn is None:
            _bad(name, "unknown check"); results[name] = False; continue
        results[name] = fn(tenant)
    print("summary:", " ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
