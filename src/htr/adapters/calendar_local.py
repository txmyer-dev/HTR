"""Own-table calendar: business hours minus booked appointments. The dev/default implementation —
a client with no calendar system still works on day one. The Google adapter replaces this behind
the same port when a tenant's config says so."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import sqlalchemy as sa

from ..config import Tenant
from ..db import appointments
from ..ports.calendar import Slot

SLOT_MINUTES = 30
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class LocalCalendar:
    def __init__(self, engine: sa.Engine, tenant: Tenant):
        self.engine = engine
        self.tenant = tenant

    def _day_window(self, day: datetime) -> tuple[datetime, datetime] | None:
        if DAY_KEYS[day.weekday()] not in self.tenant.business_hours.days:
            return None
        h1, m1 = map(int, self.tenant.business_hours.start.split(":"))
        h2, m2 = map(int, self.tenant.business_hours.end.split(":"))
        tz = day.tzinfo or timezone.utc
        return (datetime.combine(day.date(), time(h1, m1), tz),
                datetime.combine(day.date(), time(h2, m2), tz))

    def available_slots(self, day: datetime, limit: int = 3) -> list[Slot]:
        window = self._day_window(day)
        if not window:
            return []
        start, end = window
        with self.engine.connect() as conn:
            taken = conn.execute(
                sa.select(appointments.c.slot_start).where(
                    appointments.c.tenant_id == self.tenant.id,
                    appointments.c.slot_start >= start,
                    appointments.c.slot_start < end)).scalars().all()
        taken_set = {t.replace(tzinfo=t.tzinfo or timezone.utc) for t in taken}
        out: list[Slot] = []
        cursor = max(start, day) if day > start else start
        # align to the slot grid
        minutes = (cursor.minute // SLOT_MINUTES) * SLOT_MINUTES
        cursor = cursor.replace(minute=minutes, second=0, microsecond=0)
        while cursor + timedelta(minutes=SLOT_MINUTES) <= end and len(out) < limit:
            if cursor not in taken_set:
                out.append(Slot(cursor, cursor + timedelta(minutes=SLOT_MINUTES)))
            cursor += timedelta(minutes=SLOT_MINUTES)
        return out

    def book(self, slot: Slot, phone: str, note: str) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(appointments.insert().values(
                tenant_id=self.tenant.id, call_id=None, phone=phone,
                slot_start=slot.start, slot_end=slot.end, note=note))
        return f"apt-{row.inserted_primary_key[0]}"
