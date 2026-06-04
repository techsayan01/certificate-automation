"""
Run lifecycle routes.

Festival-user side
──────────────────
  GET  /festival/send             upload form
  POST /festival/send             upload CSV → create Run → dispatch
  GET  /festival/runs             list runs for this festival
  GET  /festival/runs/{id}        dashboard for a single run
  GET  /festival/runs/{id}/status JSON poll endpoint for the dashboard
  POST /festival/runs/{id}/cancel marks queued/running runs as failed

Worker side
───────────
  POST /worker/run/{id}           Cloud Tasks (or dev BG task) entry point.
                                  Verifies X-Worker-Token before dispatching.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.auth.service import require_festival_user
from backend.app.db.client import MongoDB
from backend.app.db.models import RunStatus, UserDoc, utc_now
from backend.app.services.dispatcher import (
    dispatch_run,
    make_worker_token,
    verify_worker_token,
)
from backend.app.services.pipeline import execute_run

router = APIRouter(tags=["runs"])
templates_renderer = Jinja2Templates(directory="backend/app/templates")


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _festival_id(user: UserDoc) -> str:
    if not user.festival_id or not ObjectId.is_valid(user.festival_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No festival assigned")
    return user.festival_id


async def _get_run_for_festival(run_id: str, festival_id: str) -> dict:
    if not ObjectId.is_valid(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    doc = await MongoDB.runs().find_one({
        "_id":         ObjectId(run_id),
        "festival_id": festival_id,
    })
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return doc


async def _get_festival(festival_id: str) -> dict:
    return await MongoDB.festivals().find_one({"_id": ObjectId(festival_id)})


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
        **extra,
    }


def _public_run(doc: dict, *, include_log: bool = False) -> dict:
    out = {
        "id":          str(doc["_id"]),
        "status":      doc.get("status", "queued"),
        "totals":      doc.get("totals", {}),
        "season":      doc.get("season", ""),
        "season_date": doc.get("season_date", ""),
        "created_at":  doc.get("created_at"),
        "started_at":  doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
    }
    if include_log:
        # Tail to keep payload small while still useful in the dashboard
        out["log"] = doc.get("log", [])[-200:]
    return out


# ── Upload + send form ───────────────────────────────────────────────────────

@router.get("/festival/send", response_class=HTMLResponse)
async def send_form(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    festival = await _get_festival(await _festival_id(user))
    gmail = festival.get("gmail") or {}
    canva = festival.get("canva") or {}

    ready = {
        "gmail": bool(gmail.get("refresh_token_enc")),
        "canva": bool(canva.get("refresh_token_enc")),
        "templates": await MongoDB.cert_templates().count_documents(
            {"festival_id": str(festival["_id"])}
        ) > 0,
    }
    return templates_renderer.TemplateResponse(
        request,
        "festival/send.html",
        _ctx(request, user, festival, ready=ready),
    )


@router.post("/festival/send")
async def send_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[UserDoc, Depends(require_festival_user)],
    csv:         Annotated[UploadFile, File()],
    season:      Annotated[str, Form()] = "",
    season_date: Annotated[str, Form()] = "",
):
    festival_id = await _festival_id(user)
    festival = await _get_festival(festival_id)
    if not festival:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Festival not found")

    # Validate readiness
    if not (festival.get("gmail") or {}).get("refresh_token_enc"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Gmail isn't connected. Connect it first.")
    if not (festival.get("canva") or {}).get("refresh_token_enc"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Canva isn't connected. Connect it first.")
    if not await MongoDB.cert_templates().count_documents({"festival_id": festival_id}):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "No certificate templates configured.")

    if not csv.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No CSV uploaded")
    csv_bytes = await csv.read()
    if not csv_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "CSV is empty")

    defaults = festival.get("defaults", {})
    season      = season      or defaults.get("season", "")
    season_date = season_date or defaults.get("season_date", "")

    result = await MongoDB.runs().insert_one({
        "festival_id":  festival_id,
        "triggered_by": str(user.id),
        "csv_filename": csv.filename,
        "csv_bytes":    csv_bytes,        # dropped after the run completes
        "season":       season,
        "season_date":  season_date,
        "status":       RunStatus.QUEUED.value,
        "totals":       {"total": 0, "sent": 0, "failed": 0, "skipped": 0,
                         "certs_skipped": 0},
        "log":          [],
        "started_at":   None,
        "finished_at":  None,
        "created_at":   utc_now(),
    })

    run_id = str(result.inserted_id)
    await dispatch_run(run_id, background_tasks=background_tasks)

    return RedirectResponse(
        url=f"/festival/runs/{run_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Runs list ────────────────────────────────────────────────────────────────

@router.get("/festival/runs", response_class=HTMLResponse)
async def list_runs(
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    festival = await _get_festival(fid)
    cursor = MongoDB.runs().find({"festival_id": fid}).sort("created_at", -1)
    runs = [_public_run(d) async for d in cursor]
    return templates_renderer.TemplateResponse(
        request,
        "festival/runs_list.html",
        _ctx(request, user, festival, runs=runs),
    )


# ── Run detail dashboard ─────────────────────────────────────────────────────

@router.get("/festival/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(
    run_id: str,
    request: Request,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    festival = await _get_festival(fid)
    run = await _get_run_for_festival(run_id, fid)
    return templates_renderer.TemplateResponse(
        request,
        "festival/run_detail.html",
        _ctx(request, user, festival, run=_public_run(run, include_log=True)),
    )


@router.get("/festival/runs/{run_id}/status")
async def run_status(
    run_id: str,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    fid = await _festival_id(user)
    run = await _get_run_for_festival(run_id, fid)
    return JSONResponse(content=_serialise_for_json(_public_run(run, include_log=True)))


def _serialise_for_json(d):
    """Recursively convert datetimes/ObjectIds → JSON-friendly strings."""
    if isinstance(d, dict):
        return {k: _serialise_for_json(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_serialise_for_json(x) for x in d]
    if isinstance(d, datetime):
        return d.isoformat()
    if isinstance(d, ObjectId):
        return str(d)
    return d


@router.post("/festival/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user: Annotated[UserDoc, Depends(require_festival_user)],
):
    """Mark a queued/running run as failed. The worker may still be
    running; once it sees the failed status it will stop logging."""
    fid = await _festival_id(user)
    run = await _get_run_for_festival(run_id, fid)
    if run.get("status") in (RunStatus.DONE.value, RunStatus.FAILED.value):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run already finished")

    await MongoDB.runs().update_one(
        {"_id": run["_id"]},
        {"$set": {
            "status": RunStatus.FAILED.value,
            "finished_at": datetime.now(timezone.utc),
        }, "$push": {
            "log": {"ts": datetime.now(timezone.utc),
                    "level": "warn", "msg": "Cancelled by user"},
        }},
    )
    return RedirectResponse(
        url=f"/festival/runs/{run_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Worker endpoint (Cloud Tasks calls this) ─────────────────────────────────

@router.post("/worker/run/{run_id}")
async def worker_run(
    run_id: str,
    x_worker_token: Annotated[str | None, Header(alias="X-Worker-Token")] = None,
):
    """Public endpoint hit by Cloud Tasks. The token guards against
    unauthorised replay; Cloud Tasks attaches it when enqueuing.
    Runs the pipeline synchronously inside the request — Cloud Run will
    let this run for up to the deployment's request timeout."""
    if not x_worker_token or not verify_worker_token(x_worker_token, run_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad worker token")

    if not ObjectId.is_valid(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    await execute_run(run_id)
    return {"ok": True}
