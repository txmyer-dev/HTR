from __future__ import annotations

import textwrap

import pytest
from fastapi.testclient import TestClient

from htr.adapters.messaging_console import ConsoleMessaging
from htr.config import TenantRegistry
from htr.db import make_engine
from htr.proposals.store import ProposalStore
from htr.web.app import create_app

OWNER = "+15550000009"
CALLER = "+15551234567"

TWO_TENANTS = textwrap.dedent("""
    tenants:
      tony:
        display_name: "Tony Myers"
        timezone: "America/Chicago"
        numbers: ["+15550000001"]
        business_hours: { start: "09:00", end: "17:00", days: [mon, tue, wed, thu, fri] }
        escalation_phone: "+15550000009"
        booking_link: "https://cal.example/tony"
        owner:
          name: Tony
          phone: "+15550000009"
          names: [Tony]
          digest_at: immediate
          default_deadline_days: 2
        adapters: { calendar: local, contacts: local, messaging: console, voice: retell, llm: template }
      # Slice 8: this tenant exists ONLY here, in the legacy shape. If any test needs code for it,
      # the design failed.
      second:
        display_name: "Second Business"
        timezone: "America/New_York"
        numbers: ["+15550000002"]
        business_hours: { start: "08:00", end: "18:00", days: [mon, tue, wed, thu, fri, sat] }
        owner_phone: "+15550000019"
        booking_link: "https://cal.example/second"
        adapters: { calendar: local, contacts: local, messaging: console, voice: retell }
""")


@pytest.fixture()
def registry(tmp_path):
    f = tmp_path / "install.yaml"
    f.write_text(TWO_TENANTS, encoding="utf-8")
    return TenantRegistry.load(f)


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("HTR_DATA_DIR", str(tmp_path / "data"))
    return make_engine(f"sqlite:///{tmp_path}/test.db")


@pytest.fixture()
def store(engine):
    return ProposalStore(engine)


@pytest.fixture()
def client(engine, registry):
    ConsoleMessaging.clear()
    return TestClient(create_app(engine, registry))


def tool_body(name: str, args: dict | None = None, call_id: str = "call-1",
              tool_call_id: str = "tc-1", from_number: str = CALLER,
              to_number: str = "+15550000001") -> dict:
    return {"name": name, "tool_call_id": tool_call_id, "args": args or {},
            "call": {"call_id": call_id, "from_number": from_number, "to_number": to_number}}


def ended_body(call_id: str = "call-1", from_number: str = CALLER,
               to_number: str = "+15550000001", transcript: str = "hi") -> dict:
    return {"call": {"call_id": call_id, "from_number": from_number,
                     "to_number": to_number, "transcript": transcript}}


def sms_form(from_number: str, body: str, sid: str = "SM1") -> dict:
    return {"From": from_number, "To": "+15550000001", "Body": body, "MessageSid": sid}


def texts_to(number: str) -> list[str]:
    return [b for (to, b) in ConsoleMessaging.sent if to == number]
