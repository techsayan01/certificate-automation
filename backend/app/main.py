"""
FastAPI entry — wires routes, static files, lifespan, bootstrap admin.

Phase 1 surface:
  /login           public — sign-in form
  /logout          authenticated — clears cookie
  /admin/festivals admin   — festival CRUD UI
  /                redirect to the right landing based on role

Bootstrap admin:
  On first boot (no users in DB), if INITIAL_ADMIN_EMAIL/PASSWORD are set in
  the environment, we create that admin user automatically. Without this you'd
  have no way to log in to a fresh deployment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.auth.service import (
    _LoginRedirect,
    hash_password,
    optional_user,
)
from backend.app.db.client import MongoDB
from backend.app.db.models import UserRole, utc_now
from backend.app.routes.admin import router as admin_router
from backend.app.routes.admin_users import router as admin_users_router
from backend.app.routes.auth import router as auth_router
from backend.app.routes.festival import router as festival_router
from backend.app.routes.oauth import router as oauth_router
from backend.app.routes.templates_crud import router as templates_router
from backend.app.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await MongoDB.connect()
    await _bootstrap_admin()
    yield
    await MongoDB.disconnect()


async def _bootstrap_admin() -> None:
    """Create the initial admin user if the users collection is empty."""
    settings = get_settings()
    if not settings.INITIAL_ADMIN_EMAIL or not settings.INITIAL_ADMIN_PASSWORD:
        return

    count = await MongoDB.users().count_documents({}, limit=1)
    if count > 0:
        return

    email = settings.INITIAL_ADMIN_EMAIL.lower().strip()
    doc = {
        "email":         email,
        "role":          UserRole.ADMIN.value,
        "festival_id":   None,
        "password_hash": hash_password(settings.INITIAL_ADMIN_PASSWORD),
        "created_at":    utc_now(),
        "updated_at":    utc_now(),
    }
    await MongoDB.users().insert_one(doc)


app = FastAPI(
    title="Certificate Automation",
    lifespan=lifespan,
)

# Static + routes
app.mount("/static", StaticFiles(directory="backend/app/static"), name="static")
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(admin_users_router)
app.include_router(festival_router)
app.include_router(templates_router)
app.include_router(oauth_router)


# ── Custom exception handler — turn _LoginRedirect into an actual redirect ───

@app.exception_handler(_LoginRedirect)
async def _login_redirect_handler(request: Request, exc: _LoginRedirect) -> Response:
    return RedirectResponse(url=exc.next_url, status_code=status.HTTP_303_SEE_OTHER)


# ── Root: redirect by role ────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    user = await optional_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.role == UserRole.ADMIN:
        return RedirectResponse(url="/admin/festivals", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/festival", status_code=status.HTTP_303_SEE_OTHER)


# ── Healthcheck for Cloud Run ─────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    try:
        if MongoDB.client is not None:
            await MongoDB.client.admin.command("ping")
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(503, f"mongo: {exc}")
