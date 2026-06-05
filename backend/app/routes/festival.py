"""
Festival user routes.

A festival_user is scoped to exactly one festival (referenced by
user.festival_id). Every route in this module loads that festival and
refuses access if it's missing or inactive.

  GET  /festival                  home — connection status, recent runs
  GET  /festival/settings         view + edit festival defaults
  POST /festival/settings         save defaults
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.auth.service import require_festival_user
from backend.app.db.client import MongoDB
from backend.app.db.models import UserDoc

router = APIRouter(prefix="/festival", tags=["festival"])
templates = Jinja2Templates(directory="backend/app/templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_festival(user: UserDoc) -> dict:
    if not user.festival_id or not ObjectId.is_valid(user.festival_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no festival assigned")
    doc = await MongoDB.festivals().find_one({"_id": ObjectId(user.festival_id)})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festival not found")
    return doc


def _connection_status(doc: dict) -> dict:
    """Surface boolean flags + sender email for the UI."""
    gmail = doc.get("gmail") or {}
    canva = doc.get("canva") or {}
    return {
        "gmail_connected":    bool(gmail.get("refresh_token_enc")),
        "gmail_client_set":   bool(gmail.get("client_id") and gmail.get("client_secret_enc")),
        "gmail_sender_email": gmail.get("sender_email", ""),
        "canva_connected":    bool(canva.get("refresh_token_enc")),
    }


def _ctx(request: Request, user: UserDoc, festival: dict, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "festival": {
            "id":   str(festival["_id"]),
            "slug": festival.get("slug", ""),
            "name": festival.get("name", ""),
            "defaults": festival.get("defaults", {}),
        },
        "connection": _connection_status(festival),
        **extra,
    }


# ── Home ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _get_festival(user)

    # Recent runs (last 5)
    runs_cursor = (
        MongoDB.runs()
        .find({"festival_id": str(festival["_id"])})
        .sort("created_at", -1)
        .limit(5)
    )
    recent_runs = [
        {
            "id":          str(r["_id"]),
            "status":      r.get("status", "queued"),
            "totals":      r.get("totals", {}),
            "season":      r.get("season", ""),
            "season_date": r.get("season_date", ""),
            "created_at":  r.get("created_at"),
        }
        async for r in runs_cursor
    ]

    # Template count
    template_count = await MongoDB.cert_templates().count_documents(
        {"festival_id": str(festival["_id"])}
    )

    return templates.TemplateResponse(
        request,
        "festival/home.html",
        _ctx(request, user, festival,
             recent_runs=recent_runs,
             template_count=template_count),
    )


# ── Settings ─────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _get_festival(user)
    return templates.TemplateResponse(
        request,
        "festival/settings.html",
        _ctx(request, user, festival),
    )


@router.post("/settings")
async def settings_save(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
    email_subject:   Annotated[str, Form()] = "",
    email_from_name: Annotated[str, Form()] = "",
    season:          Annotated[str, Form()] = "",
    season_date:     Annotated[str, Form()] = "",
):
    festival = await _get_festival(user)
    await MongoDB.festivals().update_one(
        {"_id": festival["_id"]},
        {"$set": {
            "defaults.email_subject":   email_subject,
            "defaults.email_from_name": email_from_name,
            "defaults.season":          season,
            "defaults.season_date":     season_date,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return RedirectResponse(url="/festival/settings", status_code=status.HTTP_303_SEE_OTHER)
