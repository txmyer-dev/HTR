from __future__ import annotations

import sqlalchemy as sa

from ..db import contacts, utcnow


class LocalContacts:
    def __init__(self, engine: sa.Engine):
        self.engine = engine

    def lookup(self, tenant_id: str, phone: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(contacts).where(
                contacts.c.tenant_id == tenant_id, contacts.c.phone == phone)).mappings().first()
        return dict(row) if row else None

    def upsert_from_call(self, tenant_id: str, phone: str, name: str | None) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(sa.select(contacts.c.id, contacts.c.call_count).where(
                contacts.c.tenant_id == tenant_id, contacts.c.phone == phone)).first()
            if existing:
                values = {"last_call_at": utcnow(), "call_count": existing.call_count + 1}
                if name:
                    values["name"] = name
                conn.execute(contacts.update().where(contacts.c.id == existing.id).values(**values))
            else:
                conn.execute(contacts.insert().values(
                    tenant_id=tenant_id, phone=phone, name=name,
                    last_call_at=utcnow(), call_count=1))
