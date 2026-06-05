"""
OAuth connect/callback routes for Gmail and Canva.

Gmail
─────
  Each festival supplies its own OAuth client.
  /festival/connect/gmail → Google OAuth → /oauth/gmail/callback
  refresh_token stored encrypted on the festival doc.

Canva
─────
  Canva credentials are PER ADMIN (client_id + client_secret stored on
  the admin's user doc, set in /admin/profile).  Each festival still
  gets its own refresh_token.  The state carries the admin's user_id so
  the callback can load the right credentials.

  PKCE verifier rides inside the signed state.

Disconnect routes wipe only the refresh_token.
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


async def _admin_canva_creds(festival: dict) -> tuple[str, str]:
    """Return (client_id, client_secret) from the admin who owns the festival."""
    admin_id = festival.get("created_by", "")
    if not admin_id or not ObjectId.is_valid(admin_id):
        return "", ""
    admin = await MongoDB.users().find_one({"_id": ObjectId(admin_id)})
    if not admin:
        return "", ""
    canva = admin.get("canva") or {}
    client_id     = canva.get("client_id", "")
    client_secret = decrypt(canva.get("client_secret_enc", ""))
    return client_id, client_secret


def _redirect_uri(provider: str) -> str:
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
            "Gmail credentials aren't configured for this festival. "
            "Ask an admin to set them."
        )

    state = make_state({"fid": str(festival["_id"]), "uid": str(user.id), "prv": "gmail"})
    params = {
        "client_id":     gmail["client_id"],
        "redirect_uri":  _redirect_uri("gmail"),
        "response_type": "code",
        "scope":         " ".join(GMAIL_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state,
    }
    return RedirectResponse(
        url=f"{GMAIL_AUTH_URL}?{urllib.parse.urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/oauth/gmail/callback")
async def gmail_callback(request: Request, code: str | None = None,
                         state: str | None = None, error: str | None = None):
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
        return _connect_error_redirect("Festival is missing Gmail credentials")

    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(GMAIL_TOKEN_URL, data={
            "code":          code,
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  _redirect_uri("gmail"),
            "grant_type":    "authorization_code",
        })
    if r.status_code != 200:
        return _connect_error_redirect(
            f"Gmail token exchange failed: {r.status_code} {r.text[:200]}")

    refresh_token = r.json().get("refresh_token")
    if not refresh_token:
        return _connect_error_redirect(
            "Google didn't return a refresh_token. Revoke access at "
            "myaccount.google.com → Security → Third-party apps, then reconnect.")

    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {"gmail.refresh_token_enc": encrypt(refresh_token),
                  "updated_at": datetime.now(timezone.utc)}},
    )
    return RedirectResponse(url="/festival/settings?ok=gmail+connected",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.post("/festival/disconnect/gmail")
async def gmail_disconnect(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _load_festival_or_403(user)
    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {"gmail.refresh_token_enc": "", "gmail.signature_html": "",
                  "updated_at": datetime.now(timezone.utc)}},
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
    festival = await _load_festival_or_403(user)
    client_id, _ = await _admin_canva_creds(festival)

    if not client_id:
        return _connect_error_redirect(
            "Canva credentials aren't configured yet. "
            "Ask your admin to add them under Admin → Profile → Canva integration."
        )

    settings = get_settings()
    verifier = random_pkce_verifier()
    state = make_state({
        "fid": str(festival["_id"]),
        "uid": str(user.id),
        "prv": "canva",
        "ver": verifier,
        # Carry the admin_id so the callback can load the right client_secret
        "aid": festival.get("created_by", ""),
    })
    params = {
        "response_type":         "code",
        "client_id":             client_id,
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
async def canva_callback(request: Request, code: str | None = None,
                         state: str | None = None, error: str | None = None):
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

    # Load Canva credentials from the admin who owns this festival
    admin_id = payload.get("aid") or festival.get("created_by", "")
    admin = await MongoDB.users().find_one({"_id": ObjectId(admin_id)}) if admin_id else None
    if not admin:
        return _connect_error_redirect("Admin not found")

    canva_creds = admin.get("canva") or {}
    client_id     = canva_creds.get("client_id", "")
    client_secret = decrypt(canva_creds.get("client_secret_enc", ""))
    if not client_id or not client_secret:
        return _connect_error_redirect(
            "Admin's Canva credentials are not configured. "
            "Admin must set them at Admin → Profile → Canva integration."
        )

    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(
            settings.CANVA_TOKEN_URL,
            auth=(client_id, client_secret),
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  _redirect_uri("canva"),
                "code_verifier": payload["ver"],
            },
        )
    if r.status_code != 200:
        return _connect_error_redirect(
            f"Canva token exchange failed: {r.status_code} {r.text[:200]}")

    refresh_token = r.json().get("refresh_token")
    if not refresh_token:
        return _connect_error_redirect("Canva didn't return a refresh_token")

    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {"canva.refresh_token_enc": encrypt(refresh_token),
                  "updated_at": datetime.now(timezone.utc)}},
    )
    return RedirectResponse(url="/festival/settings?ok=canva+connected",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.post("/festival/disconnect/canva")
async def canva_disconnect(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _load_festival_or_403(user)
    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {"canva.refresh_token_enc": "",
                  "updated_at": datetime.now(timezone.utc)}},
    )
    return RedirectResponse(url="/festival/settings", status_code=status.HTTP_303_SEE_OTHER)
