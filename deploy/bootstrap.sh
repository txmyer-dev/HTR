#!/usr/bin/env bash
# One-shot deploy on a fresh Ubuntu/Debian VPS. Idempotent — safe to re-run for updates.
# Usage (as root):  bash deploy/bootstrap.sh
set -euo pipefail
REPO=https://github.com/txmyer-dev/htr.git
DIR=/opt/htr

echo "== packages =="
apt-get update -qq
apt-get install -y -qq git python3 python3-venv curl
# One front door per box: this VPS runs Dokploy's Traefik on 80/443, so no Caddy here.
# (An earlier revision installed Caddy; neutralize it if present.)
systemctl disable --now caddy 2>/dev/null || true

echo "== user + code =="
id -u htr &>/dev/null || useradd --system --home "$DIR" --shell /usr/sbin/nologin htr
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
mkdir -p "$DIR/data"

echo "== venv =="
if [ ! -x "$DIR/.venv/bin/python" ]; then
  python3 -m venv "$DIR/.venv" 2>/dev/null || {
    python3 -m venv --without-pip "$DIR/.venv"
    curl -sS https://bootstrap.pypa.io/get-pip.py | "$DIR/.venv/bin/python" - -q
  }
fi
"$DIR/.venv/bin/pip" install -q fastapi uvicorn sqlalchemy pyyaml httpx
chown -R htr:htr "$DIR"

echo "== config =="
mkdir -p /etc/htr
[ -f /etc/htr/env ] || { cp "$DIR/deploy/env.example" /etc/htr/env; chmod 600 /etc/htr/env; }

echo "== services =="
cp "$DIR"/deploy/htr-{web,worker}.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now htr-web htr-worker

sleep 2
echo "== health =="
curl -fsS localhost:8001/healthz && echo && echo "OK — now fill /etc/htr/env and: systemctl restart htr-web htr-worker"
