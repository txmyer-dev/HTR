"""One-time consent for Gmail: prints a URL, catches the redirect on localhost, and writes the
refresh token to .env. Run on a machine with a browser, then copy .env to the install.

    python -m htr.gmail_auth [--port 8765]

Needs GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env. A "Desktop app" OAuth client
accepts any loopback redirect; a "Web application" client must have the exact redirect registered
under Authorized redirect URIs (default here: http://127.0.0.1:8765, no trailing slash). Scopes are
gmail.readonly and gmail.send, nothing wider; re-consenting drops anything broader."""
from __future__ import annotations

import argparse
import http.server
import os
import re
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from .env import load_dotenv

SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--redirect", default=None, help="exact redirect URI registered on the client "
                    "(default http://127.0.0.1:<port>, no trailing slash; Google matches byte for byte)")
    args = ap.parse_args(argv)
    load_dotenv()
    cid, csec = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""), os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not (cid and csec):
        sys.exit("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be in .env first")
    redirect = args.redirect or f"http://127.0.0.1:{args.port}"
    state = secrets.token_urlsafe(16)
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Open this URL, sign in as the owner's Gmail, approve:\n\n" + url + "\n")
    webbrowser.open(url)
    got: dict[str, str] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            got.update(q)
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<p>Hold the Room: consent received. You can close this tab.</p>")
        def log_message(self, *a): pass

    with http.server.HTTPServer(("127.0.0.1", args.port), H) as srv:
        while "code" not in got and "error" not in got:
            srv.handle_request()
    if "error" in got or got.get("state") != state:
        sys.exit(f"consent failed: {got}")
    r = httpx.post(TOKEN, data={"code": got["code"], "client_id": cid, "client_secret": csec,
                                "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    refresh = tok.get("refresh_token")
    if not refresh:
        sys.exit("no refresh token returned; revoke the app at myaccount.google.com/permissions and run again")
    me = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                   headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=20).json()
    env = Path(os.environ.get("HTR_ENV_FILE") or Path(__file__).resolve().parents[2] / ".env")
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    for k, v in (("GOOGLE_REFRESH_TOKEN", refresh), ("GMAIL_ADDRESS", me.get("emailAddress", ""))):
        if re.search(rf"^{k}=", text, re.M):
            text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{k}={v}\n"
    env.write_text(text, encoding="utf-8")
    print(f"ok: refresh token and GMAIL_ADDRESS={me.get('emailAddress')} written to {env}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
