from __future__ import annotations

from typing import Protocol


class ContactsPort(Protocol):
    def lookup(self, tenant_id: str, phone: str) -> dict | None:
        """Known-caller lookup. Read-only, in-call safe."""
        ...

    def upsert_from_call(self, tenant_id: str, phone: str, name: str | None) -> None:
        """Post-call only. CRM-backed implementations (incl. third-party tool layers) plug in
        here and nowhere in-call (SPEC R5)."""
        ...
