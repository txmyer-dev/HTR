"""The web surface (STEALS: switchboard web/app.py, plus the SMS webhooks and the proposals view).

In-call tool webhook and call-ended, as before. New: POST /webhooks/{tenant}/sms is where a
ruling arrives (from the owner's number) or an inbound text lands (from anyone else), and
POST /webhooks/{tenant}/sms-status takes delivery receipts. Everything slow is a job."""
from __future__ import annotations

import hmac
import logging
import os
import uuid
from typing import Any
from urllib.parse import parse_qsl

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import adapters
from ..config import TenantRegistry
from ..env import load_dotenv
from ..db import calls, jobs as jobs_t, make_engine, messages, proposals as proposals_t, utcnow
from ..events import append_event, record_action
from ..jobs import enqueue, make_rulings
from ..rulings.controller import ruling_secret, ruling_token
from ..rulings.parse import Verdict
from ..tools import dispatch_tool

log = logging.getLogger("htr.web")
TWIML_EMPTY = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response></Response>"


def create_app(engine: sa.Engine | None = None,
               registry: TenantRegistry | None = None) -> FastAPI:
    if engine is None or registry is None:
        load_dotenv()
    engine = engine or make_engine()
    registry = registry or TenantRegistry.load()
    app = FastAPI(title="hold-the-room", version="0.1.0")

    def tenant_or_404(tenant_id: str):
        try:
            return registry.get(tenant_id)
        except KeyError:
            raise HTTPException(404, "unknown tenant")

    @app.get("/healthz")
    def healthz() -> dict:
        with engine.connect() as conn:
            conn.execute(sa.select(sa.literal(1)))
        return {"ok": True, "tenants": len(registry.all())}

    # ---- voice (stolen) ------------------------------------------------------

    @app.post("/webhooks/{tenant_id}/tool")
    async def tool_webhook(tenant_id: str, request: Request) -> dict:
        tenant = tenant_or_404(tenant_id)
        voice = adapters.voice_for(tenant)
        raw = await request.body()
        if not voice.verify(tenant.secrets.get("voice_webhook_secret"),
                            dict(request.headers), raw):
            raise HTTPException(401, "bad signature")
        body: dict[str, Any] = await request.json()
        tc = voice.parse_tool_call(body)
        with engine.begin() as conn:
            exists = conn.execute(sa.select(calls.c.id).where(calls.c.id == tc.call_id)).first()
            if not exists and tc.call_id:
                conn.execute(calls.insert().values(
                    id=tc.call_id, tenant_id=tenant.id, from_number=tc.from_number,
                    to_number=tc.to_number, started_at=utcnow()))
                append_event(conn, tenant.id, "call.started",
                             {"from": tc.from_number}, call_id=tc.call_id)
        return dispatch_tool(engine, tenant, tc)

    @app.post("/webhooks/{tenant_id}/call-ended")
    async def call_ended(tenant_id: str, request: Request) -> dict:
        tenant = tenant_or_404(tenant_id)
        voice = adapters.voice_for(tenant)
        raw = await request.body()
        if not voice.verify(tenant.secrets.get("voice_webhook_secret"),
                            dict(request.headers), raw):
            raise HTTPException(401, "bad signature")
        ended = voice.parse_call_ended(await request.json())
        with engine.begin() as conn:
            row = conn.execute(sa.select(calls.c.id, calls.c.ended_at)
                               .where(calls.c.id == ended.call_id)).first()
            if row is None:
                conn.execute(calls.insert().values(
                    id=ended.call_id, tenant_id=tenant.id, from_number=ended.from_number,
                    to_number=ended.to_number, started_at=utcnow()))
                row = (ended.call_id, None)
            already_ended = row[1] is not None
            conn.execute(calls.update().where(calls.c.id == ended.call_id).values(
                ended_at=utcnow(),
                summary=(ended.transcript[:500] or None)))
            append_event(conn, tenant.id, "call.ended",
                         {"duplicate": already_ended}, call_id=ended.call_id)
            if not already_ended:      # webhook may arrive twice; enqueue post_call once
                enqueue(conn, tenant.id, "post_call", {"call_id": ended.call_id})
        return {"ok": True, "duplicate": already_ended}

    # ---- SMS: rulings in, texts in ----------------------------------------------

    @app.post("/webhooks/{tenant_id}/sms")
    async def sms_webhook(tenant_id: str, request: Request) -> Response:
        tenant = tenant_or_404(tenant_id)
        raw = await request.body()
        params = dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        messenger = adapters.messaging_for(tenant)
        verify = getattr(messenger, "verify_inbound", None)
        if verify is not None:
            public = os.environ.get("HTR_PUBLIC_URL", "").rstrip("/")
            url = (public + request.url.path) if public else str(request.url)
            if not verify(url, params, request.headers.get("x-twilio-signature", "")):
                raise HTTPException(401, "bad signature")
        frm = params.get("From", "").strip()
        body = params.get("Body", "").strip()
        sid = params.get("MessageSid") or f"local-{uuid.uuid4().hex[:12]}"
        if not frm:
            raise HTTPException(400, "missing From")
        with engine.begin() as conn:
            append_event(conn, tenant.id, "sms.received", {"from": frm, "sid": sid, "chars": len(body)})

        if frm == tenant.owner.phone:
            make_rulings(engine).handle_owner_reply(tenant, frm, body)
            return Response(TWIML_EMPTY, media_type="application/xml")

        with engine.begin() as conn:
            if record_action(conn, tenant.id, f"sms:{sid}", "receive_sms", {"from": frm}):
                res = conn.execute(messages.insert().values(
                    tenant_id=tenant.id, call_id=None, from_number=frm, body=body, at=utcnow(),
                    channel="sms", direction="in", vendor_ref=sid))
                message_id = int(res.inserted_primary_key[0])
                enqueue(conn, tenant.id, "draft", {"source_kind": "sms", "message_id": message_id})
        return Response(TWIML_EMPTY, media_type="application/xml")

    @app.post("/webhooks/{tenant_id}/sms-status")
    async def sms_status(tenant_id: str, request: Request) -> Response:
        tenant = tenant_or_404(tenant_id)
        raw = await request.body()
        params = dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        with engine.begin() as conn:
            append_event(conn, tenant.id, "sms.status",
                         {"sid": params.get("MessageSid"), "status": params.get("MessageStatus")})
        return Response(status_code=204)

    # ---- rulings by button ------------------------------------------------------------

    @app.api_route("/rulings/{tenant_id}/{proposal_id}/{verdict}", methods=["GET", "POST"], response_class=HTMLResponse)
    def rule_by_link(tenant_id: str, proposal_id: int, verdict: str, t: str = "") -> str:
        """The target of a Send / Skip button. The token binds tenant, proposal, and verdict;
        without the secret nobody can forge one, and a leaked link cannot rule on anything else."""
        tenant = tenant_or_404(tenant_id)
        if verdict not in ("sign", "reject"):
            raise HTTPException(400, "verdict must be sign or reject")
        secret = ruling_secret(tenant)
        if not secret or not hmac.compare_digest(t, ruling_token(secret, tenant.id, proposal_id, verdict)):
            raise HTTPException(401, "bad ruling token")
        note = make_rulings(engine).rule_by_id(tenant, proposal_id, Verdict(verdict))
        return (f"<html><body style='font-family:system-ui;margin:2rem;color:#1d222c'>"
                f"<p>{note}</p></body></html>")

    # ---- read-only ------------------------------------------------------------------

    @app.get("/tenants/{tenant_id}/calls", response_class=HTMLResponse)
    def call_log(tenant_id: str) -> str:
        """The read-only page that makes the retainer visible."""
        tenant = tenant_or_404(tenant_id)
        with engine.connect() as conn:
            rows = conn.execute(sa.select(calls).where(calls.c.tenant_id == tenant.id)
                                .order_by(calls.c.started_at.desc()).limit(100)).mappings().all()
            msgs = conn.execute(sa.select(messages).where(messages.c.tenant_id == tenant.id)
                                .order_by(messages.c.at.desc()).limit(100)).mappings().all()
        msg_by_call: dict[str, list[str]] = {}
        for m in msgs:
            msg_by_call.setdefault(m["call_id"] or "", []).append(m["body"])
        trs = "".join(
            f"<tr><td>{r['started_at']:%Y-%m-%d %H:%M}</td><td>{r['from_number']}</td>"
            f"<td>{r['outcome'] or ('ended' if r['ended_at'] else 'in progress')}</td>"
            f"<td>{'<br>'.join(msg_by_call.get(r['id'], [])) or '—'}</td></tr>"
            for r in rows)
        return (f"<html><head><title>{tenant.display_name} — call log</title>"
                "<style>body{font-family:system-ui;margin:2rem;color:#1d222c}"
                "table{border-collapse:collapse;width:100%}td,th{border:1px solid #c9c8c1;"
                "padding:8px;text-align:left}th{background:#e8e7e1}</style></head><body>"
                f"<h1>{tenant.display_name} — calls</h1>"
                "<table><tr><th>When</th><th>From</th><th>Outcome</th><th>Messages</th></tr>"
                f"{trs}</table></body></html>")

    @app.get("/tenants/{tenant_id}/proposals")
    def proposal_list(tenant_id: str) -> list[dict]:
        tenant = tenant_or_404(tenant_id)
        with engine.connect() as conn:
            rows = conn.execute(sa.select(
                proposals_t.c.id, proposals_t.c.status, proposals_t.c.to_number, proposals_t.c.to_name,
                proposals_t.c.summary, proposals_t.c.digest_no, proposals_t.c.deadline,
                proposals_t.c.edited, proposals_t.c.created_at, proposals_t.c.sent_at)
                .where(proposals_t.c.tenant_id == tenant.id)
                .order_by(proposals_t.c.id.desc()).limit(100)).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/tenants/{tenant_id}/jobs")
    def job_status(tenant_id: str) -> list[dict]:
        tenant = tenant_or_404(tenant_id)
        with engine.connect() as conn:
            rows = conn.execute(sa.select(jobs_t.c.id, jobs_t.c.kind, jobs_t.c.status,
                                          jobs_t.c.attempts, jobs_t.c.error)
                                .where(jobs_t.c.tenant_id == tenant.id)
                                .order_by(jobs_t.c.id.desc()).limit(50)).mappings().all()
        return [dict(r) for r in rows]

    return app


app = None   # built lazily by uvicorn factory: uvicorn "htr.web.app:create_app" --factory
