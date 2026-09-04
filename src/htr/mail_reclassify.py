"""Re-run the automated-mail rule over inbound email already recorded, and withdraw pending
drafts for anything the rule now calls automated. Used after tightening the rule.

    python -m htr.mail_reclassify [tenant] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

from .adapters.mail_gmail import is_automated
from .config import TenantRegistry
from .db import make_engine, messages
from .env import load_dotenv
from .events import append_event
from .proposals.store import PENDING, ProposalStore


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("tenant", nargs="?"); ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    load_dotenv()
    registry = TenantRegistry.load()
    tenant = registry.get(args.tenant) if args.tenant else registry.all()[0]
    engine = make_engine(); store = ProposalStore(engine)
    withdrawn = flagged = 0
    with engine.connect() as conn:
        rows = conn.execute(sa.select(messages).where(
            messages.c.tenant_id == tenant.id, messages.c.channel == "email", messages.c.direction == "in")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        now_auto = is_automated(r["from_number"], meta.get("subject", ""), {}, [])
        if now_auto and not meta.get("automated"):
            flagged += 1
            print(f"automated now: {r['from_number']:<36} {meta.get('subject', '')[:50]!r}")
            if not args.dry_run:
                with engine.begin() as conn:
                    conn.execute(messages.update().where(messages.c.id == r["id"]).values(meta={**meta, "automated": True}))
            p = store.by_source(tenant.id, "email", str(r["id"]))
            if p and p.status == PENDING:
                withdrawn += 1
                if not args.dry_run:
                    store.supersede(p.id)
                    with engine.begin() as conn:
                        append_event(conn, tenant.id, "proposal.withdrawn", {"proposal_id": p.id, "reason": "automated sender"})
    # drafts addressed to an automated sender, whatever their source (re-lists included)
    for p in store.pending(tenant.id):
        if p.kind == "email" and is_automated(p.to_number, p.meta.get("subject", ""), {}, []):
            withdrawn += 1
            print(f"withdraw #{p.id}: {p.to_number} ({p.source_kind})")
            if not args.dry_run:
                store.supersede(p.id)
                with engine.begin() as conn:
                    append_event(conn, tenant.id, "proposal.withdrawn", {"proposal_id": p.id, "reason": "automated sender"})
    print(f"{'would flag' if args.dry_run else 'flagged'} {flagged} message(s); {'would withdraw' if args.dry_run else 'withdrew'} {withdrawn} pending draft(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
