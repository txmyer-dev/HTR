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
5. **Gmail** (`adapters.mail: gmail`): put the OAuth client id and secret in `.env`, then
   `python -m htr.gmail_auth` on a machine with a browser. It asks for gmail.readonly and
   gmail.send only and writes the refresh token to `.env`. A "Web application" client must have
   `http://127.0.0.1:8765` (no trailing slash) under its authorized redirect URIs. The worker
   pulls every five minutes; Gmail's own categories decide what is a person writing to you.
   `python -m htr.mail_reclassify` re-applies the rule after you tighten it.
6. `python -m htr.check` probes every account read-only (ntfy sends one push).
7. Text `?` to your own number's assistant, or tap a button. If a digest (or "Nothing waiting
   on you.") comes back, the loop is closed.

## Deploy behind n8n (client zero)

HTR never gets a public hostname. n8n is the front door: its webhook nodes forward Retell's
calls and the ntfy ruling buttons to `http://htr-web:8000` on the proxy's overlay network.

```bash
# on the VPS
git clone git@github.com:txmyer-dev/HTR.git /opt/htr && cd /opt/htr && cp /path/to/.env .env
docker compose -f compose.yml -f deploy/compose.vps.yml up -d --build
docker cp deploy/n8n-front-door.json n8n:/tmp/ && docker exec n8n n8n import:workflow --input=/tmp/n8n-front-door.json
```

Then `HTR_PUBLIC_URL=https://<n8n>/webhook/htr` makes the Retell tool URLs
`https://<n8n>/webhook/htr/webhooks/<tenant>/tool`, and `HTR_RULING_URL=https://<n8n>/webhook/htr/rule`
makes each Send / Skip button a signed link the workflow forwards to `/rulings/...`.

## The surface

| Route | Called by | Does |
|---|---|---|
| `POST /webhooks/{tenant}/tool` | Retell, in-call | the six v1 tools, every failure degrades to take-message |
| `POST /webhooks/{tenant}/call-ended` | Retell | stores the transcript, enqueues post-call once |
| `POST /webhooks/{tenant}/sms` | Twilio | from the owner's number: a ruling; from anyone else: an inbound text |
| `POST /webhooks/{tenant}/sms-status` | Twilio | delivery receipts |
| `GET|POST /rulings/{tenant}/{id}/{sign|reject}?t=` | the Send / Skip buttons (via n8n) | one tap rules; token-signed |
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
