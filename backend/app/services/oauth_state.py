"""
Signed OAuth state parameter.

Why this exists
───────────────
The OAuth `state` value comes back from the provider unchanged. We use
it to remember which festival the user clicked "Connect" for, and to
ensure the callback wasn't forged or stolen from a different session.

`itsdangerous` gives us a signed-token implementation in the stdlib's
style: the payload is publicly readable but cannot be altered without
the signing key, and it expires after a short window.

Payload shape
─────────────
    { "fid": "<festival_id>",          # which festival is being connected
      "uid": "<user_id>",              # who clicked connect
      "prv": "canva" | "gmail",        # provider — guards against cross-provider replay
      "ver": "<pkce_code_verifier>" }  # only for Canva (PKCE)
"""

from __future__ import annotations

import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.app.settings import get_settings


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().SESSION_SECRET,
        salt="cert-automation-oauth-state",
    )


def make_state(payload: dict[str, Any]) -> str:
    """Sign and encode the OAuth state payload."""
    return _serializer().dumps(payload)


def read_state(token: str, expected_provider: str, max_age_seconds: int = 600) -> dict[str, Any] | None:
    """Validate a returned OAuth state. Returns None if invalid or expired.
    The `expected_provider` guard prevents a state minted for one provider
    being replayed against another."""
    try:
        data = _serializer().loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("prv") != expected_provider:
        return None
    return data


def random_pkce_verifier() -> str:
    """RFC 7636 §4.1 — 43-128 chars of URL-safe random."""
    return secrets.token_urlsafe(96)[:128]
