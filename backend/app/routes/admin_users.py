"""
Admin users CRUD.

Admins can invite festival users and bind them to a festival.

  GET    /admin/users                list
  GET    /admin/users/new            create form
  POST   /admin/users                create
  POST   /admin/users/{id}/delete    delete (cannot delete the last admin)

Only admins can also be created here. Admin password resets and full edit
flows are deferred until we need them.
"""

from __future__ import annotations

from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.auth.service import hash_password, require_admin
from backend.app.db.client import MongoDB
from backend.app.db.models import UserDoc, UserRole, utc_now

router = APIRouter(prefix="/admin/users", tags=["admin-users"])
templates = Jinja2Templates(directory="backend/app/templates")


def _ctx(request: Request, user: UserDoc, **extra) -> dict:
    return {"request": request, "user": user, **extra}


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_users(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    cursor = MongoDB.users().find().sort("created_at", -1)
    users = []
    festival_ids = set()
    async for doc in cursor:
        fid = doc.get("festival_id")
        if fid:
            festival_ids.add(fid)
        users.append({
            "id":          str(doc["_id"]),
            "email":       doc.get("email", ""),
            "role":        doc.get("role", ""),
            "festival_id": fid,
            "created_at":  doc.get("created_at"),
        })

    # Resolve festival names in one query
    festival_names: dict[str, str] = {}
    if festival_ids:
        valid_ids = [ObjectId(f) for f in festival_ids if ObjectId.is_valid(f)]
        async for f in MongoDB.festivals().find({"_id": {"$in": valid_ids}}):
            festival_names[str(f["_id"])] = f.get("name", "")

    for u in users:
        u["festival_name"] = festival_names.get(u["festival_id"] or "", "")

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        _ctx(request, user, users=users),
    )


# ── Create form ──────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_user_form(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    # Fetch festivals for the role=festival_user dropdown
    festivals = []
    async for f in MongoDB.festivals().find({"status": "active"}).sort("name", 1):
        festivals.append({"id": str(f["_id"]), "name": f.get("name", "")})

    return templates.TemplateResponse(
        request,
        "admin/user_form.html",
        _ctx(request, user, festivals=festivals),
    )


@router.post("")
async def create_user(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
    email:       Annotated[str, Form()],
    password:    Annotated[str, Form()],
    role:        Annotated[str, Form()],
    festival_id: Annotated[str, Form()] = "",
):
    if role not in (UserRole.ADMIN.value, UserRole.FESTIVAL_USER.value):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid role")

    if role == UserRole.FESTIVAL_USER.value:
        if not festival_id or not ObjectId.is_valid(festival_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "festival_id required for festival_user")
        # Confirm festival exists
        if not await MongoDB.festivals().find_one({"_id": ObjectId(festival_id)}):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Festival not found")
        bound_festival_id = festival_id
    else:
        bound_festival_id = None

    if len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at least 8 chars")

    email = email.lower().strip()
    if await MongoDB.users().find_one({"email": email}):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"User '{email}' already exists")

    await MongoDB.users().insert_one({
        "email":         email,
        "role":          role,
        "festival_id":   bound_festival_id,
        "password_hash": hash_password(password),
        "created_at":    utc_now(),
        "updated_at":    utc_now(),
    })

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.post("/{user_id}/delete")
async def delete_user(
    user_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if str(user.id) == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Refusing to delete the currently signed-in account")

    target = await MongoDB.users().find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # Don't let the last admin be deleted
    if target.get("role") == UserRole.ADMIN.value:
        remaining = await MongoDB.users().count_documents({"role": UserRole.ADMIN.value})
        if remaining <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Cannot delete the last remaining admin")

    await MongoDB.users().delete_one({"_id": ObjectId(user_id)})
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)
