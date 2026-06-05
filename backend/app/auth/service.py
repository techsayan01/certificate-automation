"""
Auth — password hashing + signed-cookie sessions.

Design notes:
  • bcrypt for passwords (cost=12 default — tunable via env if needed).
  • Sessions are signed cookies (itsdangerous), not server-side records.
    The only state in the cookie is the user_id; we re-read the user from
    Mongo on each request. This means revoking a user is instant.
  • The cookie is HttpOnly + SameSite=Lax. Set Secure=True under HTTPS.
  • A separate JSON-API path (Bearer token) is intentionally out of scope
    here — Phase 1 is admin-only, all browser-driven.

Dependencies exposed:
  • require_user      — any logged-in user
  • require_admin     — admin only
  • require_festival  — festival_user attached to a festival
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.app.db.client import MongoDB
from backend.app.db.models import UserDoc, UserRole
from backend.app.settings import get_settings


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # Malformed stored hash — treat as failure rather than crashing
        return False


# ── Session cookie ────────────────────────────────────────────────────────────

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().SESSION_SECRET,
        salt="cert-automation-session",
    )


def make_session_token(user_id: str) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> str | None:
    try:
        data = _serializer().loads(token, max_age=get_settings().SESSION_MAX_AGE)
        return data.get("uid")
    except (BadSignature, SignatureExpired):
        return None


# ── FastAPI dependencies ──────────────────────────────────────────────────────

async def _get_user_from_cookie(request: Request) -> UserDoc | None:
    cookie_name = get_settings().SESSION_COOKIE
    token = request.cookies.get(cookie_name)
    if not token:
        return None

    user_id = read_session_token(token)
    if not user_id:
        return None

    from bson import ObjectId
    if not ObjectId.is_valid(user_id):
        return None

    doc = await MongoDB.users().find_one({"_id": ObjectId(user_id)})
    if not doc:
        return None

    # Normalise _id → string before constructing the model
    doc["_id"] = str(doc["_id"])
    if doc.get("festival_id") is not None:
        doc["festival_id"] = str(doc["festival_id"])
    return UserDoc.model_validate(doc)


async def optional_user(request: Request) -> UserDoc | None:
    """Use when a route renders different content for guests vs logged-in."""
    return await _get_user_from_cookie(request)


async def require_user(request: Request) -> UserDoc:
    user = await _get_user_from_cookie(request)
    if not user:
        # For HTML routes redirect, for API routes raise 401.
        # We use the Accept header to disambiguate.
        if "text/html" in request.headers.get("accept", ""):
            raise _redirect_to_login(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


async def require_admin(user: Annotated[UserDoc, Depends(require_user)]) -> UserDoc:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


async def require_festival_user(
    user: Annotated[UserDoc, Depends(require_user)],
) -> UserDoc:
    if user.role != UserRole.FESTIVAL_USER or not user.festival_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Festival user access required")
    return user


# ── Redirect helper ───────────────────────────────────────────────────────────

class _LoginRedirect(HTTPException):
    """Hack so a Depends() can short-circuit with a redirect."""
    def __init__(self, next_url: str):
        super().__init__(status.HTTP_303_SEE_OTHER, detail="Redirect to login")
        self.next_url = next_url


def _redirect_to_login(request: Request) -> _LoginRedirect:
    next_url = request.url.path
    return _LoginRedirect(f"/login?next={next_url}")
