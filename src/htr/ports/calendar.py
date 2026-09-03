from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


class CalendarPort(Protocol):
    def available_slots(self, day: datetime, limit: int = 3) -> list[Slot]:
        """Open slots on the given day, soonest first. Must answer inside the in-call budget."""
        ...

    def book(self, slot: Slot, phone: str, note: str) -> str:
        """Book the slot; returns a booking reference. One cheap write (SPEC R2)."""
        ...
