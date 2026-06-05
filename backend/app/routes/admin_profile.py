"""
Admin profile routes.

  GET  /admin/profile           view profile + Canva connection status
  POST /admin/profile/canva     save Canva client_id + client_secret
  POST /admin/profile/password  change password
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.auth.service import hash_password, require_admin, verify_password
from backend.app.db.client import MongoDB
from backend.app.db.models import UserDoc
from backend.app.services.crypto import decrypt, encrypt

router = APIRouter(prefix="/admin/profile", tags=["admin-profile"])
templates = Jinja2Templates(directory="backend/app/templates")


def _ctx(request: Request, user: UserDoc, **extra) -> dict:
    return {"request": request, "user": user, **extra}


@router.get("", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    # Load fresh doc to get canva sub-doc
    doc = await MongoDB.users().find_one({"_id": ObjectId(str(user.id))})
    canva = (doc or {}).get("canva") or {}
    return templates.TemplateResponse(
        request,
        "admin/profile.html",
        _ctx(
            request, user,
            canva_client_id=canva.get("client_id", ""),
            canva_configured=bool(canva.get("client_id") and canva.get("client_secret_enc")),
        ),
    )


@router.post("/canva")
async def save_canva_credentials(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
    canva_client_id:     Annotated[str, Form()],
    canva_client_secret: Annotated[str, Form()] = "",
):
    """Save this admin's Canva integration credentials.
    Leaving client_secret blank keeps the existing one."""
    update: dict = {
        "canva.client_id": canva_client_id.strip(),
        "updated_at":      datetime.now(timezone.utc),
    }
    if canva_client_secret.strip():
        update["canva.client_secret_enc"] = encrypt(canva_client_secret.strip())

    await MongoDB.users().update_one(
        {"_id": ObjectId(str(user.id))},
        {"$set": update},
    )
    return RedirectResponse(
        url="/admin/profile?ok=canva+credentials+saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/password")
async def change_password(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
    current_password: Annotated[str, Form()],
    new_password:     Annotated[str, Form()],
):
    doc = await MongoDB.users().find_one({"_id": ObjectId(str(user.id))})
    if not doc or not verify_password(current_password, doc.get("password_hash", "")):
        return RedirectResponse(
            url="/admin/profile?error=Current+password+is+incorrect",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(new_password) < 8:
        return RedirectResponse(
            url="/admin/profile?error=New+password+must+be+at+least+8+characters",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    await MongoDB.users().update_one(
        {"_id": ObjectId(str(user.id))},
        {"$set": {
            "password_hash": hash_password(new_password),
            "updated_at":    datetime.now(timezone.utc),
        }},
    )
    return RedirectResponse(
        url="/admin/profile?ok=Password+changed",
        status_code=status.HTTP_303_SEE_OTHER,
    )
