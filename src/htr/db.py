"""Schema and engine (STEALS: switchboard db.py, plus proposals, rulings, digests).

SQLite by default (dev, tests); Postgres via HTR_DATABASE_URL in production. SQLAlchemy Core
only: the spine is tables and constraints, not an object model.

Two constraints carry the idempotency rule:
- events is append-only by convention (no update/delete path exists in the codebase), and
- actions has the unique idempotency key that makes replayed webhooks harmless.

Three tables are this repo's own: proposals (the lifecycle), rulings (what the owner said and
what it meant), and digests (the numbering a ruling resolves against).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import sqlalchemy as sa

metadata = sa.MetaData()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone-aware columns; everything we store is UTC,
    so a naive value read back is UTC. Postgres returns them aware already."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


calls = sa.Table(
    "calls", metadata,
    sa.Column("id", sa.String, primary_key=True),            # vendor call id
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("from_number", sa.String, nullable=False),
    sa.Column("to_number", sa.String, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.Column("outcome", sa.String),                          # booked | message | transferred | info | abandoned
    sa.Column("summary", sa.Text),
)

events = sa.Table(
    "events", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("call_id", sa.String, index=True),
    sa.Column("kind", sa.String, nullable=False),             # call.started, tool.check_availability, ...
    sa.Column("payload", sa.JSON, nullable=False, default=dict),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
)

actions = sa.Table(
    "actions", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("idempotency_key", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),             # book_appointment, take_message, send_sms ...
    sa.Column("payload", sa.JSON, nullable=False, default=dict),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_actions_idempotency"),
)

contacts = sa.Table(
    "contacts", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("phone", sa.String, nullable=False),
    sa.Column("name", sa.String),
    sa.Column("last_call_at", sa.DateTime(timezone=True)),
    sa.Column("call_count", sa.Integer, nullable=False, default=0),
    sa.UniqueConstraint("tenant_id", "phone", name="uq_contacts_phone"),
)

messages = sa.Table(
    "messages", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("call_id", sa.String, index=True),
    sa.Column("from_number", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("channel", sa.String, nullable=False, default="call"),     # call | sms
    sa.Column("direction", sa.String, nullable=False, default="in"),     # in | out
    sa.Column("vendor_ref", sa.String),                                  # e.g. Twilio MessageSid, Gmail id
    sa.Column("meta", sa.JSON, nullable=False, default=dict),            # email: subject, thread_id, message_id, name
)

appointments = sa.Table(
    "appointments", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("call_id", sa.String, index=True),
    sa.Column("phone", sa.String, nullable=False),
    sa.Column("slot_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("slot_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("note", sa.Text),
)

jobs = sa.Table(
    "jobs", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("kind", sa.String, nullable=False),             # post_call, draft, digest, dispatch, expire, heartbeat
    sa.Column("payload", sa.JSON, nullable=False, default=dict),
    sa.Column("status", sa.String, nullable=False, default="pending", index=True),  # pending|done|failed
    sa.Column("attempts", sa.Integer, nullable=False, default=0),
    sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("error", sa.Text),
)

# ---------------------------------------------------------------- this repo's own

proposals = sa.Table(
    "proposals", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("kind", sa.String, nullable=False, default="sms"),         # sms (email later)
    sa.Column("to_number", sa.String, nullable=False),
    sa.Column("to_name", sa.String),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("basis", sa.JSON, nullable=False, default=list),           # verbatim quotes the draft rests on
    sa.Column("summary", sa.String, nullable=False),
    # pending -> signed -> sending -> sent ; pending -> rejected | expired ; sending -> failed -> signed (retry)
    sa.Column("status", sa.String, nullable=False, default="pending", index=True),
    sa.Column("source_kind", sa.String),                                 # call_message | sms
    sa.Column("source_id", sa.String),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("digest_no", sa.Integer),
    sa.Column("digest_id", sa.Integer),
    sa.Column("edited", sa.Boolean, nullable=False, default=False),
    sa.Column("sent_ref", sa.String),
    sa.Column("error", sa.Text),
    sa.Column("meta", sa.JSON, nullable=False, default=dict),            # email: subject, thread_id, in_reply_to
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ruled_at", sa.DateTime(timezone=True)),
    sa.Column("sent_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("tenant_id", "source_kind", "source_id", name="uq_proposals_source"),
)

rulings = sa.Table(
    "rulings", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("proposal_id", sa.Integer, index=True),
    sa.Column("digest_id", sa.Integer),
    sa.Column("from_number", sa.String, nullable=False),
    sa.Column("raw", sa.Text, nullable=False),
    sa.Column("verdict", sa.String, nullable=False),          # sign | reject | edit | command:<name> | ignored
    sa.Column("edit_body", sa.Text),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
)

digests = sa.Table(
    "digests", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.String, nullable=False, index=True),
    sa.Column("mapping", sa.JSON, nullable=False, default=dict),          # {"1": proposal_id, ...}
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
)


ADDITIVE_COLUMNS = (                      # columns added after a table first shipped
    ("messages", "meta", "JSON"), ("proposals", "meta", "JSON"),
)


def make_engine(url: str | None = None) -> sa.Engine:
    url = url or os.environ.get("HTR_DATABASE_URL", "sqlite:///htr.db")
    engine = sa.create_engine(url, future=True)
    metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: sa.Engine) -> None:
    """create_all never alters an existing table. Additive columns are applied here so an
    install upgrades in place; a real migration tool arrives when a change is not additive."""
    insp = sa.inspect(engine)
    with engine.begin() as conn:
        for table, col, typ in ADDITIVE_COLUMNS:
            if table in insp.get_table_names() and col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
