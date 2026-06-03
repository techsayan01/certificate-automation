"""
Run dispatcher — schedules pipeline execution.

Two backends:
  • In development (ENV != "production"), we run the pipeline inline using
    FastAPI's BackgroundTasks. The HTTP request returns immediately, the
    worker continues in the same Python process. Good enough for laptops.
  • In production, we enqueue a Cloud Task that hits POST /worker/run/{id}
    after the response is sent. The actual Cloud Run instance handling the
    task is allowed to take up to its timeout (we cap at 60 min) to drive
    the pipeline to completion.

Why this split exists
─────────────────────
  A single HTTP request on Cloud Run can run for at most 60 minutes
  (and most deployments set a much tighter timeout). Sending hundreds of
  certificates can take 20+ minutes. Doing it in-band would either block
  the user's browser or get killed mid-flight. Cloud Tasks decouples the
  trigger from the execution.

Token verification
──────────────────
  The /worker/run/{id} endpoint is publicly addressable but it requires a
  matching `X-Worker-Token` header. We sign the run_id with the session
  secret and require the same signature on inbound. Cloud Tasks attaches
  this header when enqueuing, so only requests we created can be honoured.
"""

from __future__ import annotations

import json

import httpx
from fastapi import BackgroundTasks
from itsdangerous import BadSignature, URLSafeTimedSerializer

from backend.app.services.pipeline import execute_run
from backend.app.settings import get_settings


def _worker_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().SESSION_SECRET,
        salt="cert-automation-worker-token",
    )


def make_worker_token(run_id: str) -> str:
    return _worker_serializer().dumps({"rid": run_id})


def verify_worker_token(token: str, run_id: str, max_age_seconds: int = 3600) -> bool:
    try:
        data = _worker_serializer().loads(token, max_age=max_age_seconds)
    except BadSignature:
        return False
    return isinstance(data, dict) and data.get("rid") == run_id


async def dispatch_run(run_id: str, background_tasks: BackgroundTasks | None = None) -> None:
    """Kick off a run.

    In dev: schedules execute_run() onto BackgroundTasks (sync to this
            process). The caller's response goes out, then the pipeline
            runs to completion.
    In prod: enqueues a Cloud Task targeting POST /worker/run/{run_id}.
    """
    settings = get_settings()

    if settings.ENV != "production":
        if background_tasks is None:
            # Fall back to firing the coroutine onto the event loop directly.
            import asyncio
            asyncio.create_task(execute_run(run_id))
        else:
            background_tasks.add_task(execute_run, run_id)
        return

    # ── Production: enqueue via Cloud Tasks ─────────────────────────────────
    # Lazy-import so we don't take the gcloud dep in dev environments.
    try:
        from google.cloud import tasks_v2     # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-tasks not installed; needed when ENV=production"
        ) from exc

    if not (settings.GCP_PROJECT and settings.CLOUD_TASKS_LOCATION
            and settings.CLOUD_TASKS_QUEUE and settings.CLOUD_TASKS_SA_EMAIL):
        raise RuntimeError(
            "Cloud Tasks not fully configured. Set GCP_PROJECT, "
            "CLOUD_TASKS_LOCATION, CLOUD_TASKS_QUEUE, CLOUD_TASKS_SA_EMAIL."
        )

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        settings.GCP_PROJECT,
        settings.CLOUD_TASKS_LOCATION,
        settings.CLOUD_TASKS_QUEUE,
    )
    worker_url = f"{settings.BASE_URL}/worker/run/{run_id}"
    body = json.dumps({"run_id": run_id}).encode()

    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url":         worker_url,
            "headers": {
                "Content-Type":   "application/json",
                "X-Worker-Token": make_worker_token(run_id),
            },
            "body": body,
            "oidc_token": {
                "service_account_email": settings.CLOUD_TASKS_SA_EMAIL,
                "audience":              settings.BASE_URL,
            },
        }
    }
    client.create_task(parent=parent, task=task)
