"""
Canva OAuth 2.0 + PKCE authentication.

First run  : opens your browser, you click "Allow", token is saved locally.
After that : loads the saved token and auto-refreshes when it expires.

Token file : controlled by CANVA_TOKEN_FILE in .env (default: canva_token.json)
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


# ── PKCE helpers ──────────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge)."""
    verifier  = secrets.token_urlsafe(96)          # ~128 URL-safe chars
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Token persistence ─────────────────────────────────────────────────────────

def _save(data: dict) -> None:
    path = Path(Config.CANVA_TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    logger.debug(f"Canva token saved → {path}")


def _load() -> dict | None:
    path = Path(Config.CANVA_TOKEN_FILE)
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def _expired(data: dict, buffer: int = 60) -> bool:
    return time.time() >= data.get("expires_at", 0) - buffer


# ── Token refresh ─────────────────────────────────────────────────────────────

def _refresh(data: dict) -> dict:
    logger.info("Canva token expired — refreshing…")
    resp = requests.post(
        Config.CANVA_TOKEN_URL,
        auth=(Config.CANVA_CLIENT_ID, Config.CANVA_CLIENT_SECRET),  # HTTP Basic Auth
        data={
            "grant_type":    "refresh_token",
            "refresh_token": data["refresh_token"],
        },
    )
    if not resp.ok:
        raise RuntimeError(
            f"Token refresh failed {resp.status_code}: {resp.text}"
        )
    new = resp.json()
    new["expires_at"] = time.time() + new.get("expires_in", 3600)
    if "refresh_token" not in new:          # Canva sometimes omits it
        new["refresh_token"] = data["refresh_token"]
    _save(new)
    logger.info("Canva token refreshed.")
    return new


# ── Full browser OAuth flow ───────────────────────────────────────────────────

def _oauth_flow() -> dict:
    """Run PKCE browser flow. Blocks until the user authorises."""
    verifier, challenge = _pkce_pair()

    params = {
        "response_type":         "code",
        "client_id":             Config.CANVA_CLIENT_ID,
        "redirect_uri":          Config.CANVA_REDIRECT_URI,
        "scope":                 Config.CANVA_SCOPES,
        "code_challenge":        challenge,
        "code_challenge_method": "s256",
    }
    auth_url = Config.CANVA_AUTH_URL + "?" + urllib.parse.urlencode(params)

    # ── local HTTP server to capture the callback ─────────────────────────────
    auth_code: list[str] = []
    error_msg: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                auth_code.append(qs["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                    <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
                    <h2 style="color:#2e7d32">&#10003; Authorised!</h2>
                    <p>You can close this tab and return to the terminal.</p>
                    </body></html>
                """)
            else:
                error_msg.append(qs.get("error", ["unknown"])[0])
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):
            pass   # silence HTTP log

    print()
    print("=" * 62)
    print("  Canva authorisation required.")
    print("  Opening your browser — click Allow to continue.")
    print(f"\n  If the browser doesn't open, visit:\n  {auth_url}")
    print("=" * 62)
    print()

    # Start the server BEFORE opening the browser so the redirect is never missed
    server = HTTPServer(("127.0.0.1", 8080), _Handler)
    server.timeout = 120

    webbrowser.open(auth_url)
    server.handle_request()

    if error_msg:
        raise RuntimeError(f"Canva OAuth error: {error_msg[0]}")
    if not auth_code:
        raise RuntimeError("No auth code received — did you authorise in the browser?")

    # ── exchange code for tokens ──────────────────────────────────────────────
    resp = requests.post(
        Config.CANVA_TOKEN_URL,
        auth=(Config.CANVA_CLIENT_ID, Config.CANVA_CLIENT_SECRET),  # HTTP Basic Auth
        data={
            "grant_type":    "authorization_code",
            "code":          auth_code[0],
            "redirect_uri":  Config.CANVA_REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    if not resp.ok:
        raise RuntimeError(
            f"Token exchange failed {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    _save(data)
    logger.info("Canva authorised successfully.")
    return data


# ── Public API ────────────────────────────────────────────────────────────────

def get_canva_token() -> str:
    """Return a valid Canva access token, running OAuth if needed."""
    data = _load()

    if data is None:
        data = _oauth_flow()
    elif _expired(data):
        try:
            data = _refresh(data)
        except Exception as exc:
            logger.warning(f"Token refresh failed ({exc}) — re-authorising…")
            Path(Config.CANVA_TOKEN_FILE).unlink(missing_ok=True)
            data = _oauth_flow()

    return data["access_token"]
