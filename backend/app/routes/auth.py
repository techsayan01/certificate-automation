"""
Login / logout routes.

GET  /login          → render form
POST /login          → verify creds, set cookie, redirect (next | role-default)
POST /logout         → clear cookie, redirect to /login

Routes use server-rendered HTML (Jinja2). The same templates are reused
by the rest of the admin/festival UI.
"""

from __future__ import annotations

from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.auth.service import (
    make_session_token,
    verify_password,
)
from backend.app.db.client import MongoDB
from backend.app.db.models import UserDoc, UserRole
from backend.app.settings import get_settings

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="backend/app/templates")


def _default_landing(user: UserDoc) -> str:
    if user.role == UserRole.ADMIN:
        return "/admin/festivals"
    return "/festival"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": next or "", "error": error},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
):
    doc = await MongoDB.users().find_one({"email": email.lower().strip()})
    if not doc or not verify_password(password, doc.get("password_hash", "")):
        return RedirectResponse(
            url=f"/login?error=Invalid+email+or+password",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    user_id = str(doc["_id"])
    doc["_id"] = user_id
    if doc.get("festival_id"):
        doc["festival_id"] = str(doc["festival_id"])
    user = UserDoc.model_validate(doc)

    token = make_session_token(user_id)
    settings = get_settings()
    redirect_to = next if next.startswith("/") else _default_landing(user)

    response = RedirectResponse(url=redirect_to, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=settings.SESSION_COOKIE,
        value=token,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(get_settings().SESSION_COOKIE)
    return response
