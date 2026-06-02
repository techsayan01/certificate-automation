"""
Certificate template CRUD for festival users.

Each template maps a (category, judging_status) pair to:
  • A Canva Brand Template ID
  • A field name map (which Canva field receives Name / Project / etc.)
  • An HTML email template body
  • An optional laurel image

The composite unique index on (festival_id, category, judging_status)
in the DB layer prevents duplicates and gives the pipeline a 1:1 lookup
at run time.

  GET    /festival/templates                list
  GET    /festival/templates/new            create form
  POST   /festival/templates                create
  GET    /festival/templates/{id}/edit      edit form
  POST   /festival/templates/{id}           update
  POST   /festival/templates/{id}/delete    delete
  POST   /festival/templates/{id}/preview   render email body with sample vars

Laurel uploads are stored under LAUREL_STORAGE_DIR/{festival_id}/{template_id}.png
in Phase 2 — Phase 3 migrates this to GCS.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment

from backend.app.auth.service import require_festival_user
from backend.app.db.client import MongoDB
from backend.app.db.models import JudgingStatus, UserDoc
from backend.app.settings import get_settings

router = APIRouter(prefix="/festival/templates", tags=["festival-templates"])
templates_renderer = Jinja2Templates(directory="backend/app/templates")


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _festival_id(user: UserDoc) -> str:
    if not user.festival_id or not ObjectId.is_valid(user.festival_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no festival assigned")
    return user.festival_id


async def _get_template_or_404(template_id: str, festival_id: str) -> dict:
    if not ObjectId.is_valid(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    doc = await MongoDB.cert_templates().find_one({
        "_id":         ObjectId(template_id),
        "festival_id": festival_id,
    })
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    return doc


def _to_public(doc: dict) -> dict:
    return {
        "id":                       str(doc["_id"]),
        "category":                 doc.get("category", ""),
        "judging_status":           doc.get("judging_status", ""),
        "canva_brand_template_id":  doc.get("canva_brand_template_id", ""),
        "canva_field_map":          doc.get("canva_field_map", {}),
        "email_template_html":      doc.get("email_template_html", ""),
        "laurel_path":              doc.get("laurel_path", ""),
        "created_at":               doc.get("created_at"),
        "updated_at":               doc.get("updated_at"),
    }


def _laurel_path_for(festival_id: str, template_id: str) -> Path:
    """Path on local disk where a laurel PNG lives."""
    base = Path(get_settings().LAUREL_STORAGE_DIR) / festival_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{template_id}.png"


def _ctx(request: Request, user: UserDoc, **extra) -> dict:
    return {"request": request, "user": user, **extra}


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_templates(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    cursor = MongoDB.cert_templates().find({"festival_id": fid}).sort(
        [("category", 1), ("judging_status", 1)]
    )
    items = [_to_public(d) async for d in cursor]
    return templates_renderer.TemplateResponse(
        request,
        "festival/templates_list.html",
        _ctx(request, user, items=items),
    )


# ── Create ───────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_template_form(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    return templates_renderer.TemplateResponse(
        request,
        "festival/template_form.html",
        _ctx(
            request, user,
            template=None,
            mode="create",
            statuses=[s.value for s in JudgingStatus],
            default_field_map={
                "name":        "Name",
                "project":     "Project",
                "category":    "Category",
                "season":      "Season",
                "season_date": "SeasonDate",
            },
        ),
    )


@router.post("")
async def create_template(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
    category:                Annotated[str, Form()],
    judging_status:          Annotated[str, Form()],
    canva_brand_template_id: Annotated[str, Form()],
    canva_field_map_json:    Annotated[str, Form()] = "{}",
    email_template_html:     Annotated[str, Form()] = "",
    laurel: UploadFile | None = File(None),
):
    fid = await _festival_id(user)

    # Validate field map JSON
    try:
        field_map = json.loads(canva_field_map_json) if canva_field_map_json.strip() else {}
        if not isinstance(field_map, dict) or not all(isinstance(v, str) for v in field_map.values()):
            raise ValueError("must be {str: str}")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"canva_field_map_json must be a JSON object of string→string ({exc})",
        )

    # Validate judging status
    try:
        JudgingStatus(judging_status)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown judging_status '{judging_status}'",
        )

    doc = {
        "festival_id":             fid,
        "category":                category.strip(),
        "judging_status":          judging_status,
        "canva_brand_template_id": canva_brand_template_id.strip(),
        "canva_field_map":         field_map,
        "email_template_html":     email_template_html,
        "laurel_path":             "",
        "created_at":              datetime.now(timezone.utc),
        "updated_at":              datetime.now(timezone.utc),
    }

    try:
        result = await MongoDB.cert_templates().insert_one(doc)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A template for this category + status already exists. ({exc})",
        )

    template_id = str(result.inserted_id)

    # Save laurel after we have the template_id so the filename matches
    if laurel and laurel.filename:
        path = _laurel_path_for(fid, template_id)
        path.write_bytes(await laurel.read())
        await MongoDB.cert_templates().update_one(
            {"_id": result.inserted_id},
            {"$set": {"laurel_path": str(path)}},
        )

    return RedirectResponse(url="/festival/templates", status_code=status.HTTP_303_SEE_OTHER)


# ── Edit ─────────────────────────────────────────────────────────────────────

@router.get("/{template_id}/edit", response_class=HTMLResponse)
async def edit_template_form(
    template_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    doc = await _get_template_or_404(template_id, fid)
    return templates_renderer.TemplateResponse(
        request,
        "festival/template_form.html",
        _ctx(
            request, user,
            template=_to_public(doc),
            mode="edit",
            statuses=[s.value for s in JudgingStatus],
            default_field_map={},
        ),
    )


@router.post("/{template_id}")
async def update_template(
    template_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
    category:                Annotated[str, Form()],
    judging_status:          Annotated[str, Form()],
    canva_brand_template_id: Annotated[str, Form()],
    canva_field_map_json:    Annotated[str, Form()] = "{}",
    email_template_html:     Annotated[str, Form()] = "",
    laurel: UploadFile | None = File(None),
):
    fid = await _festival_id(user)
    doc = await _get_template_or_404(template_id, fid)

    try:
        field_map = json.loads(canva_field_map_json) if canva_field_map_json.strip() else {}
        if not isinstance(field_map, dict) or not all(isinstance(v, str) for v in field_map.values()):
            raise ValueError("must be {str: str}")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"canva_field_map_json invalid: {exc}")

    update = {
        "category":                category.strip(),
        "judging_status":          judging_status,
        "canva_brand_template_id": canva_brand_template_id.strip(),
        "canva_field_map":         field_map,
        "email_template_html":     email_template_html,
        "updated_at":              datetime.now(timezone.utc),
    }

    if laurel and laurel.filename:
        path = _laurel_path_for(fid, template_id)
        path.write_bytes(await laurel.read())
        update["laurel_path"] = str(path)

    try:
        await MongoDB.cert_templates().update_one(
            {"_id": doc["_id"]},
            {"$set": update},
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Another template with the same category + status already exists. ({exc})",
        )

    return RedirectResponse(url="/festival/templates", status_code=status.HTTP_303_SEE_OTHER)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.post("/{template_id}/delete")
async def delete_template(
    template_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    doc = await _get_template_or_404(template_id, fid)

    # Clean up laurel file on disk if any
    laurel_path = doc.get("laurel_path", "")
    if laurel_path:
        try:
            Path(laurel_path).unlink(missing_ok=True)
        except OSError:
            pass     # best-effort

    await MongoDB.cert_templates().delete_one({"_id": doc["_id"]})
    return RedirectResponse(url="/festival/templates", status_code=status.HTTP_303_SEE_OTHER)


# ── Email body preview ───────────────────────────────────────────────────────

# Sample variables used when rendering the email preview. Keep these in sync
# with what the pipeline injects into the Jinja2 context at run time.
_PREVIEW_SAMPLE = {
    "name":        "Renato Santana",
    "project":     "Hunting Fireflies",
    "category":    "Best Short Film (Main Category)",
    "season":      "Season 5",
    "season_date": "Sep - Jan 2026",
    "email":       "preview@example.com",
}


@router.post("/{template_id}/preview", response_class=HTMLResponse)
async def preview_email_body(
    template_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    doc = await _get_template_or_404(template_id, fid)

    body = doc.get("email_template_html", "")
    try:
        env = Environment(autoescape=False)
        rendered = env.from_string(body).render(**_PREVIEW_SAMPLE)
    except Exception as exc:
        rendered = f"<pre style='color:#b91c1c'>Template error: {exc}</pre>"

    return HTMLResponse(content=rendered)
