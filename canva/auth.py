"""
Canva OAuth2 token manager.

Flow (first run):
  1. Opens the browser at Canva's authorisation URL.
  2. Spins up a temporary localhost HTTP server to catch the redirect.
  3. Exchanges the auth-code for access + refresh tokens.
  4. Saves tokens to CANVA_TOKEN_FILE (default: canva_token.json).

Subsequent runs:
  - Loads tokens from file.
  - Silently refreshes if the access token is expired.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional

import requests

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

# ── PKCE helpers ──────────────────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Callback server ───────────────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures ?code=… from the OAuth redirect."""
    auth_code: Optional[str] = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"<h2>Authorised! You can close this tab.</h2>"
        )

    def log_message(self, *args):  # suppress noisy request logs
        pass


def _wait_for_code(port: int, timeout: int = 120) -> str:
    server = HTTPServer(("localhost", port), _CallbackHandler)
    server.timeout = timeout
    server.handle_request()
    code = _CallbackHandler.auth_code
    if not code:
        raise RuntimeError("No authorisation code received from Canva.")
    return code


# ── Token manager ─────────────────────────────────────────────────────────────

class CanvaTokenManager:
    def __init__(self):
        self._token_file = Config.CANVA_TOKEN_FILE
        self._tokens: dict = {}

    # ── public ────────────────────────────────────────────────────────────────

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing or re-authorising as needed."""
        self._load()
        if self._is_expired():
            if self._tokens.get("refresh_token"):
                self._refresh()
            else:
                self._authorize()
        return self._tokens["access_token"]

    # ── private ───────────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._token_file):
            with open(self._token_file) as fh:
                self._tokens = json.load(fh)

    def _save(self):
        with open(self._token_file, "w") as fh:
            json.dump(self._tokens, fh, indent=2)

    def _is_expired(self) -> bool:
        if not self._tokens.get("access_token"):
            return True
        expires_at = self._tokens.get("expires_at", 0)
        return time.time() >= expires_at - 60  # 60-second buffer

    def _authorize(self):
        """Full OAuth2 PKCE flow — opens browser, waits for redirect."""
        port = int(urllib.parse.urlparse(Config.CANVA_REDIRECT_URI).port or 8080)
        verifier, challenge = _generate_pkce()
        state = secrets.token_urlsafe(16)

        params = {
            "client_id": Config.CANVA_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": Config.CANVA_REDIRECT_URI,
            "scope": Config.CANVA_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = Config.CANVA_AUTH_URL + "?" + urllib.parse.urlencode(params)

        logger.info("Opening browser for Canva authorisation…")
        webbrowser.open(auth_url)

        code = _wait_for_code(port)
        self._exchange_code(code, verifier)

    def _exchange_code(self, code: str, verifier: str):
        resp = requests.post(
            Config.CANVA_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": Config.CANVA_REDIRECT_URI,
                "code_verifier": verifier,
            },
            auth=(Config.CANVA_CLIENT_ID, Config.CANVA_CLIENT_SECRET),
            timeout=30,
        )
        resp.raise_for_status()
        self._store(resp.json())
        logger.info("Canva tokens saved.")

    def _refresh(self):
        resp = requests.post(
            Config.CANVA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._tokens["refresh_token"],
            },
            auth=(Config.CANVA_CLIENT_ID, Config.CANVA_CLIENT_SECRET),
            timeout=30,
        )
        if resp.status_code == 400:
            logger.warning("Canva refresh token invalid — re-authorising…")
            self._tokens = {}
            self._authorize()
            return
        resp.raise_for_status()
        self._store(resp.json())

    def _store(self, data: dict):
        self._tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", self._tokens.get("refresh_token")),
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
        }
        self._save()
