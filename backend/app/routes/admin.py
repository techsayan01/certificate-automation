"""
Admin routes — festival CRUD.

Each route is protected by the require_admin dependency.

  GET    /admin/festivals             list festivals
  GET    /admin/festivals/new         create form
  POST   /admin/festivals             create
  GET    /admin/festivals/{id}/edit   edit form
  POST   /admin/festivals/{id}        update
  POST   /admin/festivals/{id}/delete delete

The form accepts Gmail credentials in plaintext; we encrypt the
client_secret before persisting (refresh_token stays empty until the
festival user runs the OAuth connect flow in Phase 2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.auth.service import require_admin
from backend.app.db.client import MongoDB
from backend.app.db.models import (
    FestivalDoc,
    FestivalDefaults,
    GmailCredentials,
    FestivalStatus,
    UserDoc,
)
from backend.app.services.crypto import encrypt

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="backend/app/templates")


def _ctx(request: Request, user: UserDoc, **extra) -> dict:
    return {"request": request, "user": user, **extra}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_festival_or_404(festival_id: str) -> dict:
    if not ObjectId.is_valid(festival_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festival not found")
    doc = await MongoDB.festivals().find_one({"_id": ObjectId(festival_id)})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festival not found")
    return doc


def _to_public(doc: dict) -> dict:
    """Strip secrets, expose computed flags. Used in list/edit views."""
    gmail = doc.get("gmail") or {}
    canva = doc.get("canva") or {}
    return {
        "id":           str(doc["_id"]),
        "slug":         doc.get("slug", ""),
        "name":         doc.get("name", ""),
        "status":       doc.get("status", "active"),
        "defaults":     doc.get("defaults", {}),
        "gmail_client_id":     gmail.get("client_id", ""),
        "gmail_project_id":    gmail.get("project_id", ""),
        "gmail_sender_email":  gmail.get("sender_email", ""),
        "gmail_connected":     bool(gmail.get("refresh_token_enc")),
        "canva_connected":     bool(canva.get("refresh_token_enc")),
        "created_at":   doc.get("created_at"),
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/festivals", response_class=HTMLResponse)
async def list_festivals(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    cursor = MongoDB.festivals().find().sort("created_at", -1)
    festivals = [_to_public(doc) async for doc in cursor]
    return templates.TemplateResponse(
        request,
        "admin/festivals.html",
        _ctx(request, user, festivals=festivals),
    )


# ── Create form ───────────────────────────────────────────────────────────────

@router.get("/festivals/new", response_class=HTMLResponse)
async def new_festival_form(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    return templates.TemplateResponse(
        request,
        "admin/festival_form.html",
        _ctx(request, user, festival=None, mode="create"),
    )


@router.post("/festivals")
async def create_festival(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
    slug:                 Annotated[str, Form()],
    name:                 Annotated[str, Form()],
    festival_status:      Annotated[str, Form()] = "active",
    gmail_client_id:      Annotated[str, Form()] = "",
    gmail_client_secret:  Annotated[str, Form()] = "",
    gmail_project_id:     Annotated[str, Form()] = "",
    gmail_sender_email:   Annotated[str, Form()] = "",
    email_subject:        Annotated[str, Form()] = "",
    email_from_name:      Annotated[str, Form()] = "",
    season:               Annotated[str, Form()] = "",
    season_date:          Annotated[str, Form()] = "",
):
    # Reject duplicate slug
    existing = await MongoDB.festivals().find_one({"slug": slug})
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Slug '{slug}' already exists")

    fest = FestivalDoc(
        slug=slug,
        name=name,
        status=FestivalStatus(festival_status),
        gmail=GmailCredentials(
            client_id=gmail_client_id,
            client_secret_enc=encrypt(gmail_client_secret),
            project_id=gmail_project_id,
            sender_email=gmail_sender_email,
        ),
        defaults=FestivalDefaults(
            email_subject=email_subject or "Congratulations — Your Certificate is Here!",
            email_from_name=email_from_name or name,
            season=season,
            season_date=season_date,
        ),
        created_by=str(user.id),
    )

    payload = fest.model_dump(by_alias=True, exclude={"id"})
    await MongoDB.festivals().insert_one(payload)

    return RedirectResponse(url="/admin/festivals", status_code=status.HTTP_303_SEE_OTHER)


# ── Edit form ─────────────────────────────────────────────────────────────────

@router.get("/festivals/{festival_id}/edit", response_class=HTMLResponse)
async def edit_festival_form(
    festival_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    doc = await _get_festival_or_404(festival_id)
    return templates.TemplateResponse(
        request,
        "admin/festival_form.html",
        _ctx(request, user, festival=_to_public(doc), mode="edit"),
    )


@router.post("/festivals/{festival_id}")
async def update_festival(
    festival_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
    name:                 Annotated[str, Form()],
    festival_status:      Annotated[str, Form()] = "active",
    gmail_client_id:      Annotated[str, Form()] = "",
    gmail_client_secret:  Annotated[str, Form()] = "",
    gmail_project_id:     Annotated[str, Form()] = "",
    gmail_sender_email:   Annotated[str, Form()] = "",
    email_subject:        Annotated[str, Form()] = "",
    email_from_name:      Annotated[str, Form()] = "",
    season:               Annotated[str, Form()] = "",
    season_date:          Annotated[str, Form()] = "",
):
    doc = await _get_festival_or_404(festival_id)

    update_set = {
        "name": name,
        "status": festival_status,
        "gmail.client_id":    gmail_client_id,
        "gmail.project_id":   gmail_project_id,
        "gmail.sender_email": gmail_sender_email,
        "defaults.email_subject":   email_subject or doc.get("defaults", {}).get("email_subject", ""),
        "defaults.email_from_name": email_from_name or name,
        "defaults.season":          season,
        "defaults.season_date":     season_date,
        "updated_at": datetime.now(timezone.utc),
    }

    # Only overwrite client_secret if a new one is provided (so editing without
    # re-typing the secret doesn't wipe it out).
    if gmail_client_secret:
        update_set["gmail.client_secret_enc"] = encrypt(gmail_client_secret)

    await MongoDB.festivals().update_one(
        {"_id": ObjectId(festival_id)},
        {"$set": update_set},
    )

    return RedirectResponse(url="/admin/festivals", status_code=status.HTTP_303_SEE_OTHER)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/festivals/{festival_id}/delete")
async def delete_festival(
    festival_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    if not ObjectId.is_valid(festival_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festival not found")

    # Soft cascade — kill associated templates and users
    await MongoDB.cert_templates().delete_many({"festival_id": festival_id})
    await MongoDB.users().update_many(
        {"festival_id": festival_id},
        {"$set": {"status": "inactive"}},
    )
    await MongoDB.festivals().delete_one({"_id": ObjectId(festival_id)})

    return RedirectResponse(url="/admin/festivals", status_code=status.HTTP_303_SEE_OTHER)
