# STEALS

The ledger. This repo is self-contained: it builds, tests, and runs from its own files and
public packages only. Every module that came from somewhere else is listed here, with where it
came from at a pinned commit and what changed in the port. This file is the only place the
source repositories are named. Nothing in the repo can reach for them.

All four sources are ours (MIT where public), so this is provenance, not permission.

| here | from | at | changed |
|---|---|---|---|
| `src/htr/config.py` | txmyer-dev/switchboard `src/switchboard/config.py` | 4071120 | package renamed; `owner:` block added (phone as identity, digest time, deadline default, tiers); `owner_phone` kept as a property for the stolen code |
| `src/htr/db.py` | switchboard `db.py` | 4071120 | `messages` gains channel/direction/vendor_ref; new tables `proposals`, `rulings`, `digests` |
| `src/htr/events.py` | switchboard `events.py` | 4071120 | unchanged |
| `src/htr/jobs.py` | switchboard `jobs.py` | 4071120 | `post_call` keeps the summary and enqueues a draft per message; new handlers `draft`, `digest`, `dispatch`, `expire`; retry backoff; `enqueue_once`, `drain` |
| `src/htr/worker.py` | switchboard `worker.py` | 4071120 | digest clock per tenant timezone; hourly expiry pass |
| `src/htr/ports/{calendar,contacts,messaging,voice}.py` | switchboard `ports/` | 4071120 | unchanged |
| `src/htr/ports/llm.py` | this repo | | new port: `draft_reply(DraftRequest) -> Draft(body, basis)` |
| `src/htr/adapters/__init__.py` | switchboard `adapters/__init__.py` | 4071120 | `llm_for` added; Twilio adapter takes tenant secrets |
| `src/htr/adapters/{calendar_local,contacts_local,messaging_ntfy,voice_retell}.py` | switchboard `adapters/` | 4071120 | unchanged |
| `src/htr/adapters/messaging_console.py` | switchboard | 4071120 | `send_sms` returns a ref; `verify_inbound` always true |
| `src/htr/adapters/messaging_twilio.py` | switchboard | 4071120 | API-key auth; inbound X-Twilio-Signature validation; per-tenant secrets |
| `src/htr/adapters/llm_template.py` | this repo | | new: deterministic offline drafter |
| `src/htr/adapters/llm_openai_compat.py` | this repo | | new: OpenAI-shaped gateway drafter; drops any basis quote not present in the source |
| `src/htr/tools/surface.py` | switchboard `tools/surface.py` | 4071120 | unchanged (the six frozen in-call tools) |
| `src/htr/web/app.py` | switchboard `web/app.py` | 4071120 | `/webhooks/{t}/sms`, `/webhooks/{t}/sms-status`, `/tenants/{t}/proposals` added |
| `src/htr/provision/retell.py` | switchboard `provision.py` | 4071120 | moved into a package; paths point at `agents/retell/prompt.md` and `retell-agents.yaml` at the repo root |
| `agents/retell/prompt.md` | switchboard `agents/retell/prompt.md` | 4071120 | unchanged |
| `deploy/*` | switchboard `deploy/` | 4071120 | unit names and paths renamed |
| `src/htr/reader/waiting.py` | txmyer-dev/waggle `src/tools/waiting.ts` | dfadcd4 | ported to Python; `latest_unanswered` added for the single-thread SMS case |
| `src/htr/reader/deadline.py` | this repo | | new: finds a named day in a message |
| `src/htr/proposals/store.py` | waggle `src/proposals/store.ts` | dfadcd4 | ported to a table; lifecycle gains signed/sending/expired; status-guarded transitions replace the in-memory update |
| `src/htr/rulings/parse.py` | waggle `src/rulings/parse.ts` | dfadcd4 | ported; the numbered form (`1 ✅`, `2 no`, `3 words`), word marks, and the four commands added |
| `src/htr/rulings/controller.py` | waggle `src/rulings/controller.ts` | dfadcd4 | ported; transport is the SMS digest out and the inbound webhook back; the stale-digest invariant kept |
| `src/htr/digest/render.py` | this repo | | new: the numbered digest |
| `src/htr/digest/expiry.py` | this repo | | new: the state none of the sources had |
| `src/htr/log/jsonl.py` | txmyer-dev/capture (the discipline, not the code) | 2026-09-03 | new: committed-text append; the database is derived |
| `tests/test_stolen_switchboard.py` | switchboard `tests/test_switchboard.py` | 4071120 | imports renamed; one fixture gains the owner block |
| `tests/test_reader.py`, `tests/test_store.py` | waggle `waiting.test.ts`, `store.test.ts` | dfadcd4 | ported with the modules |

Not stolen yet (release 2): icm-agent's Model Armor screen and Vertex client (`bin/screen.mjs`,
`src/vertex.mjs`), the Basis drafter prompt, capture's Fireflies puller, icm-agent's Gmail sender.
