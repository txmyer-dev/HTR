# Deploying to the VPS (sb.felaniam.cloud)

1. **DNS**: A record `sb.felaniam.cloud` → the VPS IP. The box's front door is Dokploy's
   Traefik (80/443) — route `sb.felaniam.cloud` there to the host service on port 8001
   (Dokploy: an external/host service pointing at `http://<docker-bridge-or-host-gateway>:8001`).
   No second reverse proxy: one front door per box.
   **Firewall**: keep 8001 closed from the WAN — the service binds 0.0.0.0 so Traefik's
   container can reach it, and the perimeter is the firewall's job.
2. **On the VPS**, as root:
   ```bash
   git clone https://github.com/txmyer-dev/htr.git /opt/htr
   bash /opt/htr/deploy/bootstrap.sh
   ```
   (Private repo: use a fine-grained read-only deploy token in the clone URL, or `gh auth login` first.)
3. Fill `/etc/htr/env` (operator phone, Twilio creds, Retell webhook secret), then
   `systemctl restart htr-web htr-worker`.
4. Verify from anywhere: `curl https://sb.felaniam.cloud/healthz`
5. Re-point the agent at the real URL (from any machine with the repo + key):
   ```bash
   RETELL_API_KEY=... HTR_PUBLIC_URL=https://sb.felaniam.cloud \
       .venv/bin/python -m htr.provision tony
   ```
6. **Updates** are `bash /opt/htr/deploy/bootstrap.sh` again (it pulls + restarts), or just
   `git -C /opt/htr pull && systemctl restart htr-web htr-worker`.

SQLite lives at `/opt/htr/data/htr.db` — one tenant, one box, WAL-friendly traffic;
Postgres via `HTR_DATABASE_URL` when a paying client makes it warranted.
