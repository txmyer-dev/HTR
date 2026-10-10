"""AgentMail provisioning: one inbox and one webhook, generated once (STEALS: htr-convex
lib/mail/agentmail.ts for the API calls; this repo's own gmail_auth.py for the write-to-.env
pattern, since a webhook secret AgentMail hands back only once is exactly that kind of value).

Standing up client zero's inbox is one command:

    AGENTMAIL_API_KEY=... HTR_PUBLIC_URL=https://sb.example.com \
        python -m htr.provision.agentmail tony [--dry-run] [--force]

AGENTMAIL_INBOX_ID, AGENTMAIL_WEBHOOK_ID, and AGENTMAIL_WEBHOOK_SECRET are written to .env.
Re-running is a no-op once all three are set. AgentMail's REST surface has no update-webhook
call, so changing HTR_PUBLIC_URL means --force, which deletes the old inbox and webhook first.

This provisions one identity against whatever is currently in .env — the same "client zero"
shape gmail_auth.py already has, not the per-tenant state file retell.py keeps
(retell-agents.yaml). A second tenant wanting its own AgentMail inbox needs that file's
pattern; not built here because there is only one tenant to provision yet.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from ..adapters.mail_agentmail import AgentMailMail
from ..config import TenantRegistry
from ..env import load_dotenv

EVENT_TYPES = ["message.received"]


def _env_file() -> Path:
    return Path(os.environ.get("HTR_ENV_FILE") or Path(__file__).resolve().parents[3] / ".env")


def _write_env(values: dict[str, str]) -> Path:
    env = _env_file()
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    for k, v in values.items():
        if re.search(rf"^{k}=", text, re.M):
            text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{k}={v}\n"
    env.write_text(text, encoding="utf-8")
    return env


def provision(tenant_id: str, dry_run: bool = False, force: bool = False) -> dict:
    registry = TenantRegistry.load()
    tenant = registry.get(tenant_id)
    base_url = os.environ.get("HTR_PUBLIC_URL", "http://localhost:8000")
    webhook_url = f"{base_url.rstrip('/')}/webhooks/{tenant.id}/mail"

    existing_inbox = os.environ.get("AGENTMAIL_INBOX_ID")
    existing_webhook = os.environ.get("AGENTMAIL_WEBHOOK_ID")
    if existing_inbox and existing_webhook and not force:
        print(f"already provisioned: inbox {existing_inbox}, webhook {existing_webhook} -> {webhook_url}")
        print("re-run with --force to delete and recreate (e.g. after HTR_PUBLIC_URL changed)")
        return {"inbox_id": existing_inbox, "webhook_id": existing_webhook}

    if dry_run:
        print(f"would create an inbox for '{tenant_id}' ({tenant.display_name}), "
              f"then a webhook on message.received -> {webhook_url}")
        if force and (existing_inbox or existing_webhook):
            print(f"would first delete webhook {existing_webhook} and inbox {existing_inbox}")
        return {"dry_run": True}

    key = os.environ.get("AGENTMAIL_API_KEY")
    if not key:
        sys.exit("AGENTMAIL_API_KEY is not set (use --dry-run to inspect the plan without it)")
    mail = AgentMailMail({"agentmail_api_key": key})

    if force and existing_webhook:
        mail.delete_webhook(existing_webhook)
        print(f"deleted webhook {existing_webhook}")
    if force and existing_inbox:
        mail.delete_inbox(existing_inbox)
        print(f"deleted inbox {existing_inbox}")

    inbox_id, address = mail.create_inbox(tenant_id, display_name=tenant.display_name)
    webhook_id, secret = mail.create_webhook(webhook_url, EVENT_TYPES, [inbox_id])

    env = _write_env({"AGENTMAIL_INBOX_ID": inbox_id, "AGENTMAIL_WEBHOOK_ID": webhook_id,
                      "AGENTMAIL_WEBHOOK_SECRET": secret})
    print(f"provisioned {tenant_id}: inbox {address} ({inbox_id}), webhook {webhook_id} -> {webhook_url}")
    print(f"written to {env}. Set adapters.mail: agentmail for {tenant_id} in install.yaml, then restart.")
    return {"inbox_id": inbox_id, "address": address, "webhook_id": webhook_id}


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tenant")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no API key needed")
    ap.add_argument("--force", action="store_true", help="delete the existing inbox/webhook first, then recreate")
    args = ap.parse_args(argv)
    provision(args.tenant, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
