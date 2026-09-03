"""The one background worker (STEALS: switchboard worker.py, plus the digest clock).

Polls the jobs table. Every HEARTBEAT_MINUTES it enqueues a heartbeat per tenant. Every minute
it checks each tenant's digest time in the tenant's own timezone and enqueues the day's digest
once there is something waiting; every hour it runs the expiry pass.

Run: python -m htr.worker
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from .config import Tenant, TenantRegistry
from .db import digests, make_engine, proposals
from .jobs import enqueue, enqueue_once, run_pending

POLL_SECONDS = 2.0
HEARTBEAT_MINUTES = 15
EXPIRE_MINUTES = 60


def digest_due(engine, tenant: Tenant, now: datetime | None = None) -> bool:
    """True once per local day, after digest_at, when something is pending."""
    if tenant.owner.digest_at == "immediate":
        return False
    tz = ZoneInfo(tenant.timezone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    hh, mm = (int(x) for x in tenant.owner.digest_at.split(":"))
    if (local.hour, local.minute) < (hh, mm):
        return False
    with engine.connect() as conn:
        pending = conn.execute(sa.select(sa.func.count()).select_from(proposals).where(
            proposals.c.tenant_id == tenant.id, proposals.c.status == "pending")).scalar()
        if not pending:
            return False
        last = conn.execute(sa.select(digests.c.sent_at).where(digests.c.tenant_id == tenant.id)
                            .order_by(digests.c.id.desc()).limit(1)).scalar()
    if last is None:
        return True
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return last.astimezone(tz).date() < local.date()


def tick(engine, registry: TenantRegistry, state: dict[str, float]) -> None:
    now = time.monotonic()
    if now - state.get("heartbeat", 0.0) > HEARTBEAT_MINUTES * 60:
        with engine.begin() as conn:
            for tenant in registry.all():
                enqueue(conn, tenant.id, "heartbeat")
        state["heartbeat"] = now
    if now - state.get("expire", 0.0) > EXPIRE_MINUTES * 60:
        with engine.begin() as conn:
            for tenant in registry.all():
                enqueue_once(conn, tenant.id, "expire")
        state["expire"] = now
    if now - state.get("digest_check", 0.0) > 60:
        for tenant in registry.all():
            if digest_due(engine, tenant):
                with engine.begin() as conn:
                    enqueue_once(conn, tenant.id, "digest")
        state["digest_check"] = now


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    engine = make_engine()
    registry = TenantRegistry.load()
    state: dict[str, float] = {}
    while True:
        tick(engine, registry, state)
        if run_pending(engine, registry) == 0:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
