"""
OAuth connect/callback routes for Gmail and Canva.

Gmail
─────
  Each festival supplies its own OAuth client (different Google Cloud
  project per festival) — gmail_client_id + gmail_client_secret are
  stored on the festival doc by the admin.

  Flow:
      /festival/connect/gmail  →  redirect to Google with state=signed({fid,uid,prv:"gmail"})
      Google → /oauth/gmail/callback?code&state
      We exchange the code, fetch refresh_token, encrypt, store on the
      festival doc, then fetch the user's signature once.

Canva
─────
  ONE Canva integration (Cert-Automate) powers every festival. The
  client_id and client_secret live in service env vars. Each festival
  still gets its own refresh_token so different festivals can authorise
  different Canva accounts.

  Flow uses PKCE — the verifier rides along inside the signed state so
  we can match it on callback.

Disconnect routes wipe just the refresh_token, leaving the client
config intact so reconnecting is one click.
"""

from __future__ import annotations

import base64
import hashlib
import urllib.parse
from datetime import datetime, timezone
from typing import Annotated

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from backend.app.auth.service import require_festival_user
from backend.app.db.client import MongoDB
from backend.app.db.models import UserDoc
from backend.app.services.crypto import decrypt, encrypt
from backend.app.services.oauth_state import (
    make_state,
    random_pkce_verifier,
    read_state,
)
from backend.app.settings import get_settings

router = APIRouter(tags=["oauth"])

GMAIL_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES    = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _load_festival_or_403(user: UserDoc) -> dict:
    if not user.festival_id or not ObjectId.is_valid(user.festival_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no festival assigned")
    doc = await MongoDB.festivals().find_one({"_id": ObjectId(user.festival_id)})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festival not found")
    return doc


def _redirect_uri(provider: str) -> str:
    """Each provider has its own callback path."""
    return f"{get_settings().BASE_URL}/oauth/{provider}/callback"


def _connect_error_redirect(message: str) -> RedirectResponse:
    qs = urllib.parse.urlencode({"error": message})
    return RedirectResponse(
        url=f"/festival/settings?{qs}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Gmail ─────────────────────────────────────────────────────────────────────

@router.get("/festival/connect/gmail")
async def gmail_connect(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _load_festival_or_403(user)
    gmail = festival.get("gmail") or {}

    if not gmail.get("client_id") or not gmail.get("client_secret_enc"):
        return _connect_error_redirect(
            "Gmail client_id / client_secret aren't configured for this festival. "
            "Ask an admin to set them."
        )

    state = make_state({
        "fid": str(festival["_id"]),
        "uid": str(user.id),
        "prv": "gmail",
    })

    params = {
        "client_id":      gmail["client_id"],
        "redirect_uri":   _redirect_uri("gmail"),
        "response_type":  "code",
        "scope":          " ".join(GMAIL_SCOPES),
        "access_type":    "offline",       # refresh_token
        "prompt":         "consent",       # force refresh_token every time
        "state":          state,
    }
    return RedirectResponse(
        url=f"{GMAIL_AUTH_URL}?{urllib.parse.urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/oauth/gmail/callback")
async def gmail_callback(request: Request, code: str | None = None, state: str | None = None,
                        error: str | None = None):
    if error:
        return _connect_error_redirect(f"Gmail OAuth declined: {error}")
    if not code or not state:
        return _connect_error_redirect("Gmail OAuth callback missing code/state")

    payload = read_state(state, expected_provider="gmail")
    if not payload:
        return _connect_error_redirect("Gmail OAuth state expired or invalid")

    festival = await MongoDB.festivals().find_one({"_id": ObjectId(payload["fid"])})
    if not festival:
        return _connect_error_redirect("Festival not found")

    gmail = festival.get("gmail") or {}
    client_id     = gmail.get("client_id", "")
    client_secret = decrypt(gmail.get("client_secret_enc", ""))
    if not client_id or not client_secret:
        return _connect_error_redirect("Festival is missing Gmail client_id/secret")

    # Exchange code → token
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            GMAIL_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     client_id,
                "client_secret": client_secret,
                "redirect_uri":  _redirect_uri("gmail"),
                "grant_type":    "authorization_code",
            },
        )
    if r.status_code != 200:
        return _connect_error_redirect(
            f"Gmail token exchange failed: {r.status_code} {r.text[:200]}"
        )

    token = r.json()
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return _connect_error_redirect(
            "Google didn't return a refresh_token. Try disconnecting at "
            "myaccount.google.com → Security → Third-party apps, then reconnect."
        )

    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {
            "gmail.refresh_token_enc": encrypt(refresh_token),
            "updated_at": datetime.now(timezone.utc),
        }},
    )

    return RedirectResponse(
        url="/festival/settings?ok=gmail+connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/festival/disconnect/gmail")
async def gmail_disconnect(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _load_festival_or_403(user)
    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {
            "gmail.refresh_token_enc": "",
            "gmail.signature_html":    "",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return RedirectResponse(url="/festival/settings", status_code=status.HTTP_303_SEE_OTHER)


# ── Canva ─────────────────────────────────────────────────────────────────────

def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@router.get("/festival/connect/canva")
async def canva_connect(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    settings = get_settings()
    if not settings.CANVA_CLIENT_ID or not settings.CANVA_CLIENT_SECRET:
        return _connect_error_redirect(
            "Canva isn't configured on this deployment. "
            "Ask an admin to set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET."
        )

    festival = await _load_festival_or_403(user)

    verifier = random_pkce_verifier()
    state = make_state({
        "fid": str(festival["_id"]),
        "uid": str(user.id),
        "prv": "canva",
        "ver": verifier,
    })

    params = {
        "response_type":         "code",
        "client_id":             settings.CANVA_CLIENT_ID,
        "redirect_uri":          _redirect_uri("canva"),
        "scope":                 settings.CANVA_SCOPES,
        "code_challenge":        _pkce_challenge(verifier),
        "code_challenge_method": "s256",
        "state":                 state,
    }
    return RedirectResponse(
        url=f"{settings.CANVA_AUTH_URL}?{urllib.parse.urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/oauth/canva/callback")
async def canva_callback(request: Request, code: str | None = None, state: str | None = None,
                        error: str | None = None):
    if error:
        return _connect_error_redirect(f"Canva OAuth declined: {error}")
    if not code or not state:
        return _connect_error_redirect("Canva OAuth callback missing code/state")

    payload = read_state(state, expected_provider="canva")
    if not payload or "ver" not in payload:
        return _connect_error_redirect("Canva OAuth state expired or invalid")

    festival = await MongoDB.festivals().find_one({"_id": ObjectId(payload["fid"])})
    if not festival:
        return _connect_error_redirect("Festival not found")

    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            settings.CANVA_TOKEN_URL,
            auth=(settings.CANVA_CLIENT_ID, settings.CANVA_CLIENT_SECRET),
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  _redirect_uri("canva"),
                "code_verifier": payload["ver"],
            },
        )
    if r.status_code != 200:
        return _connect_error_redirect(
            f"Canva token exchange failed: {r.status_code} {r.text[:200]}"
        )

    token = r.json()
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return _connect_error_redirect("Canva didn't return a refresh_token")

    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {
            "canva.refresh_token_enc": encrypt(refresh_token),
            "updated_at": datetime.now(timezone.utc),
        }},
    )

    return RedirectResponse(
        url="/festival/settings?ok=canva+connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/festival/disconnect/canva")
async def canva_disconnect(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _load_festival_or_403(user)
    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {
            "canva.refresh_token_enc": "",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return RedirectResponse(url="/festival/settings", status_code=status.HTTP_303_SEE_OTHER)
