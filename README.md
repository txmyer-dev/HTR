# Hold the Room

An assistant that reads everything, drafts everything, and sends nothing until you rule.

A call comes in; the agent answers and takes the message. A text comes in; the reader notices it
waits on you. Each one becomes a proposal with a draft reply. At five, or right away, you get one
numbered text. You reply `1`, `2 no`, `3 tell them Tuesday works`. What you signed goes out as
you. What you edited is logged in your own words. Nothing goes out otherwise.

**Below the latency line** the assistant acts alone: answers, takes a message, checks the
calendar, books. **Above it**, every write is a proposal that waits for a reply from your number.

## One repo, self-contained

Nothing here imports from any other repository. The pieces that were proven elsewhere were
stolen once and are owned here; `STEALS.md` is the ledger. `pytest` runs with the network off.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'     # Windows: .venv\Scripts\pip
.venv/bin/pytest -q                                           # the whole loop, offline

# the service and the worker (two processes, one database)
.venv/bin/python -m uvicorn --factory htr.web.app:create_app --port 8000
.venv/bin/python -m htr.worker
```

SQLite by default; `HTR_DATABASE_URL` for Postgres. `compose.yml` runs web, worker, and Postgres
behind whatever reverse proxy the host already has.

## Configure an install

1. **`install.yaml`** is the one file that varies per client. The `owner.phone` is the identity
   every ruling is checked against. `digest_at` is local time or `immediate`.
2. **`.env`** from `.env.example`: Twilio, Retell, the LLM gateway if any. Names only in the
   yaml, values only in the env.
3. **Twilio**: point the number's inbound SMS webhook at `POST {HTR_PUBLIC_URL}/webhooks/{tenant}/sms`
   and status callbacks at `/webhooks/{tenant}/sms-status`. Set `HTR_PUBLIC_URL` exactly; the
   signature is computed over it. A2P 10DLC registration is the long pole. Start it first.
4. **Retell**: `python -m htr.provision.retell tony --dry-run`, then without the flag. The agent
   is generated from `agents/retell/prompt.md` and the six tool schemas, pointed at
   `/webhooks/{tenant}/tool` and `/webhooks/{tenant}/call-ended`.
5. Text `?` to your own number's assistant. If a digest (or "Nothing waiting on you.") comes
   back, the loop is closed.

## The surface

| Route | Called by | Does |
|---|---|---|
| `POST /webhooks/{tenant}/tool` | Retell, in-call | the six v1 tools, every failure degrades to take-message |
| `POST /webhooks/{tenant}/call-ended` | Retell | stores the transcript, enqueues post-call once |
| `POST /webhooks/{tenant}/sms` | Twilio | from the owner's number: a ruling; from anyone else: an inbound text |
| `POST /webhooks/{tenant}/sms-status` | Twilio | delivery receipts |
| `GET /tenants/{tenant}/calls` | you | the read-only call log |
| `GET /tenants/{tenant}/proposals` | you | every proposal and its status |
| `GET /tenants/{tenant}/jobs` | you | job status |
| `GET /healthz` | the proxy | liveness |

## Rulings

| Reply | Meaning |
|---|---|
| `1` · `1 ✅` · `1 yes` | sign draft 1 and send it as you |
| `2 no` · `2 ❌` | reject draft 2; nothing sent |
| `3 tell them Tuesday works` | replace draft 3's words with those, then send |
| `all` | sign every draft in the last digest |
| `later` | leave everything for the next digest |
| `hold` · `hold until 9` | hold the room; drafts keep stacking |
| `?` | send the digest now |

A reply from any number other than the owner's is logged and ignored.

## Layout

```
install.yaml              the tenant block                  src/htr/reader/     what waits on you (waggle)
.env.example              secret names                       src/htr/proposals/  the lifecycle (waggle)
STEALS.md                 the ledger                         src/htr/rulings/    parse + apply (waggle)
src/htr/config.py         install file → Tenant (switchboard) src/htr/digest/     the numbered SMS, expiry
src/htr/db.py             the spine + proposals/rulings/digests src/htr/log/      rulings.jsonl (capture's rule)
src/htr/jobs.py           post_call · draft · digest · dispatch · expire · heartbeat
src/htr/web/app.py        the webhooks                       tests/              runs offline, every module
src/htr/tools/surface.py  the six in-call tools (switchboard)
```
