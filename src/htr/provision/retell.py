"""Vendor-side provisioning (SPEC R5 discipline for the agent itself).

The Retell agent is generated from tenant config, never hand-assembled in a dashboard: the
prompt is a repo template, the six tool schemas are built here, and the result is pushed
through Retell's API. Standing up client N's agent is one command — R1 extended to the
vendor's side of the fence.

Written against Retell's API as documented (create-retell-llm / create-agent, Bearer auth).
Run --dry-run first on a fresh key: if their schema has drifted, the fix belongs here and
nowhere else — this file and voice_retell.py are the only two that know Retell exists.

Usage:
    RETELL_API_KEY=... HTR_PUBLIC_URL=https://sb.example.com \
        python -m htr.provision tony [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import yaml

from ..config import Tenant, TenantRegistry
from ..env import load_dotenv

RETELL_BASE = "https://api.retellai.com"
PROMPT_TEMPLATE = Path(__file__).resolve().parents[3] / "agents" / "retell" / "prompt.md"
STATE_FILE = Path(__file__).resolve().parents[3] / "retell-agents.yaml"

DEFAULT_VOICE = "11labs-Adrian"


def render_prompt(tenant: Tenant) -> str:
    from .. import adapters as _adapters
    agent = tenant.adapters.get("agent_name") or "Ekko"
    hours = (f"{', '.join(d.capitalize() for d in tenant.business_hours.days)} "
             f"{tenant.business_hours.start}–{tenant.business_hours.end} ({tenant.timezone})")
    can_text = getattr(_adapters.messaging_for(tenant), "can_text_callers", False)
    if can_text:
        texting_rule = ""
        send_link_route = "   - Easier by text (directions, booking page) → send_link.\n"
    else:
        texting_rule = ("6. **This line cannot send text messages.** Never offer to text "
                        "anyone anything. If a caller asks for a text, say the information "
                        "aloud instead and take a message so "
                        f"{tenant.display_name.split()[0]} can send it directly.")
        send_link_route = ""
    return PROMPT_TEMPLATE.read_text(encoding="utf-8").format(
        agent_name=agent,
        display_name=tenant.display_name,
        owner_first_name=tenant.display_name.split()[0],
        business_hours=hours,
        texting_rule=texting_rule,
        send_link_route=send_link_route,
    )


def build_tools(tenant: Tenant, base_url: str) -> list[dict]:
    """The six v1 tools as Retell custom functions, plus the built-in transfer that completes
    what transfer_to_human records. The URL is the whole integration."""
    url = f"{base_url.rstrip('/')}/webhooks/{tenant.id}/tool"

    def custom(name: str, description: str, parameters: dict, speak_during: bool = True) -> dict:
        return {
            "type": "custom", "name": name, "description": description, "url": url,
            "speak_during_execution": speak_during, "speak_after_execution": True,
            "parameters": {"type": "object", **parameters},
        }

    return [
        custom("lookup_caller",
               "Silently check whether this caller is already known. Call once, at the start.",
               {"properties": {}, "required": []}, speak_during=False),
        custom("check_availability",
               "Get open appointment slots for a day. The only source of availability.",
               {"properties": {"day": {"type": "string",
                "description": "ISO date the caller asked about, e.g. 2026-08-24"}},
                "required": ["day"]}),
        custom("book_appointment",
               "Book one slot previously returned by check_availability.",
               {"properties": {
                   "slot_start": {"type": "string",
                                  "description": "ISO start time of the chosen slot, exactly as returned"},
                   "note": {"type": "string", "description": "What the appointment is about"}},
                "required": ["slot_start"]}),
        custom("take_message",
               "Record a message for the owner: caller name, need, callback number.",
               {"properties": {"message": {"type": "string",
                "description": "The message, including name and callback number"}},
                "required": ["message"]}),
        custom("transfer_to_human",
               "Record that the caller asked for a person. Call this, then transfer the call.",
               {"properties": {"reason": {"type": "string"}}, "required": []}),
        custom("send_link",
               "Text the caller the booking link.",
               {"properties": {}, "required": []}),
    ] + transfer_tool(tenant)


def transfer_tool(tenant: Tenant) -> list[dict]:
    """The built-in transfer needs a REAL destination — Retell validates it. A +1555 placeholder
    means the tenant's numbers aren't provisioned yet; skip loudly rather than fail the whole
    provision, and re-run once install.yaml holds the real escalation number."""
    if tenant.escalation_phone.startswith("+1555"):
        print(f"WARNING: {tenant.id}: escalation_phone {tenant.escalation_phone} is a placeholder — "
              "transfer_call tool SKIPPED; re-provision when the real number is in install.yaml")
        return []
    return [
        {"type": "transfer_call", "name": "complete_transfer",
         "description": "Complete the handoff after transfer_to_human.",
         "transfer_destination": {"type": "predefined", "number": tenant.escalation_phone},
         "transfer_option": {"type": "cold_transfer"}},
    ]


def llm_payload(tenant: Tenant, base_url: str) -> dict:
    return {
        "general_prompt": render_prompt(tenant),
        "general_tools": build_tools(tenant, base_url),
        "begin_message": f"Thanks for calling {tenant.display_name}, this is "
                         f"{tenant.adapters.get('agent_name', 'Ekko')}. How can I help?",
    }


def agent_payload(tenant: Tenant, llm_id: str) -> dict:
    return {
        "agent_name": f"htr-{tenant.id}",
        "voice_id": tenant.adapters.get("voice_id", DEFAULT_VOICE),
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "webhook_url": None,   # call-ended goes to /webhooks/{tenant}/call-ended — set post-create
    }


def provision(tenant_id: str, dry_run: bool = False) -> dict:
    registry = TenantRegistry.load()
    tenant = registry.get(tenant_id)
    base_url = os.environ.get("HTR_PUBLIC_URL", "http://localhost:8000")
    llm = llm_payload(tenant, base_url)

    if dry_run:
        print(json.dumps({"llm": llm, "note": "agent payload follows llm creation"}, indent=2))
        return {"dry_run": True}

    key = os.environ.get("RETELL_API_KEY")
    if not key:
        sys.exit("RETELL_API_KEY is not set (use --dry-run to inspect payloads without it)")
    headers = {"Authorization": f"Bearer {key}"}

    state = yaml.safe_load(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    state = state or {}
    existing = state.get(tenant_id, {})

    with httpx.Client(base_url=RETELL_BASE, headers=headers, timeout=30) as client:
        if existing.get("llm_id"):
            r = client.patch(f"/update-retell-llm/{existing['llm_id']}", json=llm)
        else:
            r = client.post("/create-retell-llm", json=llm)
        r.raise_for_status()
        llm_id = r.json()["llm_id"]

        agent = agent_payload(tenant, llm_id)
        agent["webhook_url"] = f"{base_url.rstrip('/')}/webhooks/{tenant.id}/call-ended"
        if existing.get("agent_id"):
            r = client.patch(f"/update-agent/{existing['agent_id']}", json=agent)
        else:
            r = client.post("/create-agent", json=agent)
        r.raise_for_status()
        agent_id = r.json()["agent_id"]

    state[tenant_id] = {"llm_id": llm_id, "agent_id": agent_id}
    # Bind the tenant's line(s) to the new agent. Retell replaced the single-agent fields with
    # weighted agent lists (deprecation 2026-03-31); one agent at weight 1 is the whole list.
    with httpx.Client(base_url=RETELL_BASE, headers={"Authorization": f"Bearer {key}"}, timeout=30) as client:
        version = client.get(f"/get-agent/{agent_id}").json().get("version", 0)
        for number in tenant.numbers:
            r = client.patch(f"/update-phone-number/{number}", json={
                "inbound_agents": [{"agent_id": agent_id, "agent_version": version, "weight": 1}],
                "nickname": f"htr {tenant.id}"})
            if r.status_code == 200:
                print(f"bound {number} -> {agent_id} (v{version})")
            else:
                print(f"could not bind {number}: HTTP {r.status_code} {r.text[:160]} (bind it in the dashboard)")
    STATE_FILE.write_text(yaml.safe_dump(state), encoding="utf-8")
    print(f"provisioned {tenant_id}: agent {agent_id} (llm {llm_id}) -> {STATE_FILE.name}")
    return state[tenant_id]


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tenant")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rendered payloads; no API key needed")
    args = ap.parse_args()
    provision(args.tenant, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
