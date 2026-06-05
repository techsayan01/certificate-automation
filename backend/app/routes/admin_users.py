"""
Admin users CRUD.

ISOLATION MODEL: each admin manages only users attached to their own
festivals. Admin 1 cannot see or delete Admin 2's festival users.

Admins can create other admins (who start with no festivals).

  GET    /admin/users                list own festival users + all admins
  GET    /admin/users/new            create form
  POST   /admin/users                create
  POST   /admin/users/{id}/delete    delete (with safeguards)
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


async def _own_festival_ids(user: UserDoc) -> list[str]:
    """Return all festival IDs owned by this admin."""
    ids = []
    async for f in MongoDB.festivals().find(
        {"created_by": str(user.id)},
        projection={"_id": 1},
    ):
        ids.append(str(f["_id"]))
    return ids


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_users(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    own_festival_ids = await _own_festival_ids(user)

    # Show:
    #  • all admin-role users (so they can see peers and create more)
    #  • festival_users attached to THIS admin's festivals only
    cursor = MongoDB.users().find({
        "$or": [
            {"role": UserRole.ADMIN.value},
            {"festival_id": {"$in": own_festival_ids}},
        ]
    }).sort("created_at", -1)

    users = []
    festival_ids_needed = set()
    async for doc in cursor:
        fid = doc.get("festival_id")
        if fid:
            festival_ids_needed.add(fid)
        users.append({
            "id":          str(doc["_id"]),
            "email":       doc.get("email", ""),
            "role":        doc.get("role", ""),
            "festival_id": fid,
            "created_at":  doc.get("created_at"),
        })

    # Resolve only own festival names
    festival_names: dict[str, str] = {}
    if festival_ids_needed:
        valid_ids = [ObjectId(f) for f in festival_ids_needed if ObjectId.is_valid(f)]
        async for f in MongoDB.festivals().find(
            {"_id": {"$in": valid_ids}, "created_by": str(user.id)}
        ):
            festival_names[str(f["_id"])] = f.get("name", "")

    for u in users:
        u["festival_name"] = festival_names.get(u["festival_id"] or "", "")
        # Flag: is this user's festival managed by me?
        u["is_mine"] = (
            u["role"] == UserRole.ADMIN.value          # admins shown to everyone
            or u["festival_id"] in own_festival_ids    # my festival users
        )

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        _ctx(request, user, users=users),
    )


# ── Create ────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_user_form(
    request: Request,
    user: Annotated[UserDoc, Depends(require_admin)],
):
    # Only show this admin's own active festivals in the dropdown
    festivals = []
    async for f in MongoDB.festivals().find(
        {"status": "active", "created_by": str(user.id)}   # ← isolation
    ).sort("name", 1):
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

    bound_festival_id = None
    if role == UserRole.FESTIVAL_USER.value:
        if not festival_id or not ObjectId.is_valid(festival_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "festival_id required for festival_user")
        # Must be one of THIS admin's festivals
        festival = await MongoDB.festivals().find_one({
            "_id":        ObjectId(festival_id),
            "created_by": str(user.id),                # ← isolation guard
        })
        if not festival:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Festival not found or not owned by you")
        bound_festival_id = festival_id

    if len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Password must be at least 8 characters")

    email = email.lower().strip()
    if await MongoDB.users().find_one({"email": email}):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"User '{email}' already exists")

    await MongoDB.users().insert_one({
        "email":         email,
        "role":          role,
        "festival_id":   bound_festival_id,
        "password_hash": hash_password(password),
        "created_at":    utc_now(),
        "updated_at":    utc_now(),
    })
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ── Delete ────────────────────────────────────────────────────────────────────

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
                            "Cannot delete your own account")

    target = await MongoDB.users().find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # Admins can only delete their own festival users, not other admins
    if target.get("role") == UserRole.FESTIVAL_USER.value:
        own_ids = await _own_festival_ids(user)
        if target.get("festival_id") not in own_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Cannot delete a user belonging to another admin's festival")

    # Protect the last admin
    if target.get("role") == UserRole.ADMIN.value:
        remaining = await MongoDB.users().count_documents({"role": UserRole.ADMIN.value})
        if remaining <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Cannot delete the last admin account")

    await MongoDB.users().delete_one({"_id": ObjectId(user_id)})
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)
