"""The install file (STEALS: switchboard config.py, extended with the owner block).

Tenants are configuration, not rows. This module is the only place that reads install.yaml;
everything else receives Tenant objects. Secrets are env-var *names* in the file and are
resolved here at load time; values never live in the file.

The owner block is what this repo adds: where rulings come from, when the digest goes out,
and how much the assistant may do alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH_ENV = "HTR_TENANTS_FILE"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "install.yaml"


@dataclass(frozen=True)
class BusinessHours:
    start: str
    end: str
    days: tuple[str, ...]


@dataclass(frozen=True)
class Owner:
    """The human who rules. Their phone number is the identity every ruling is checked against."""
    name: str
    phone: str
    digest_at: str = "17:00"                 # local HH:MM, or "immediate"
    quiet_hours: tuple[str, str] | None = None
    default_deadline_days: int = 2
    voice_rulings: bool = False              # release 2
    autonomy_tier: int = 0                   # 0 = everything above the line is ruled
    disclose_assistant: bool = True
    summary_after_call: bool = True          # the switchboard behaviour, kept
    names: tuple[str, ...] = ()              # how people address the owner (the reader uses these)
    email: str | None = None                 # the owner's mailbox (intake reads it, replies go out as it)


@dataclass(frozen=True)
class Tenant:
    id: str
    display_name: str
    timezone: str
    numbers: tuple[str, ...]
    business_hours: BusinessHours
    owner: Owner
    escalation_phone: str
    booking_link: str
    adapters: dict[str, str]
    secrets: dict[str, str | None] = field(default_factory=dict)

    @property
    def owner_phone(self) -> str:
        """Kept for the stolen switchboard code, which addresses the owner by phone only."""
        return self.owner.phone


def _owner_from(t: dict) -> Owner:
    raw = t.get("owner")
    if isinstance(raw, dict):
        qh = raw.get("quiet_hours")
        return Owner(
            name=raw.get("name", t.get("display_name", "the owner")),
            phone=raw["phone"],
            digest_at=str(raw.get("digest_at", "17:00")),
            quiet_hours=(qh[0], qh[1]) if qh else None,
            default_deadline_days=int(raw.get("default_deadline_days", 2)),
            voice_rulings=bool(raw.get("voice_rulings", False)),
            autonomy_tier=int(raw.get("autonomy_tier", 0)),
            disclose_assistant=bool(raw.get("disclose_assistant", True)),
            summary_after_call=bool(raw.get("summary_after_call", True)),
            names=tuple(raw.get("names") or [raw.get("name")] if raw.get("name") else []),
            email=(raw.get("email") or None),
        )
    # legacy switchboard shape: owner_phone at the top level
    return Owner(name=t.get("display_name", "the owner"), phone=t["owner_phone"])


class TenantRegistry:
    def __init__(self, tenants: dict[str, Tenant], by_number: dict[str, Tenant]):
        self._tenants = tenants
        self._by_number = by_number

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TenantRegistry":
        path = Path(path or os.environ.get(CONFIG_PATH_ENV) or DEFAULT_PATH)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        tenants: dict[str, Tenant] = {}
        by_number: dict[str, Tenant] = {}
        for tid, t in (raw.get("tenants") or {}).items():
            bh = t.get("business_hours", {})
            owner = _owner_from(t)
            tenant = Tenant(
                id=tid,
                display_name=t["display_name"],
                timezone=t.get("timezone", "UTC"),
                numbers=tuple(t.get("numbers", [])),
                business_hours=BusinessHours(
                    start=bh.get("start", "09:00"),
                    end=bh.get("end", "17:00"),
                    days=tuple(bh.get("days", ["mon", "tue", "wed", "thu", "fri"])),
                ),
                owner=owner,
                escalation_phone=t.get("escalation_phone", owner.phone),
                booking_link=t.get("booking_link", ""),
                adapters=dict(t.get("adapters", {})),
                secrets={k: os.environ.get(v) for k, v in (t.get("secrets") or {}).items()},
            )
            tenants[tid] = tenant
            for n in tenant.numbers:
                by_number[n] = tenant
        return cls(tenants, by_number)

    def get(self, tenant_id: str) -> Tenant:
        return self._tenants[tenant_id]

    def by_number(self, number: str) -> Tenant | None:
        return self._by_number.get(number)

    def all(self) -> list[Tenant]:
        return list(self._tenants.values())
