"""
Canva API client — Mongo-fed, refresh-token-based, async.

This service replaces the CLI's file-based token storage (canva_token.json).
Each instance is bound to ONE festival; it loads that festival's encrypted
refresh_token from the document we were given and re-encrypts the new
refresh_token back into Mongo whenever Canva issues one.

Design choices
──────────────
  • We don't cache access tokens in memory across runs. Each pipeline
    instantiates a fresh CanvaClient, refreshes once at start, and the
    token lives for the duration of the run.
  • On 401 from a request, we refresh and retry once. After that, we
    surface the failure — there's no point looping if Canva insists the
    token is bad.
  • All HTTP is async via httpx so the Cloud Tasks worker can run many
    jobs concurrently if it ever needs to.

Public surface
──────────────
    client = await CanvaClient.for_festival(festival_id)
    design_id = await client.autofill(brand_template_id, title, data)
    url       = await client.export_pdf(design_id)
    pdf_bytes = await client.download(url)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from bson import ObjectId

from backend.app.db.client import MongoDB
from backend.app.services.crypto import decrypt, encrypt
from backend.app.settings import get_settings


class CanvaAuthError(RuntimeError):
    """Refresh token missing, expired, or rejected by Canva."""


class CanvaApiError(RuntimeError):
    """Any non-2xx response from Canva that we couldn't recover from."""


class CanvaClient:
    """Async Canva REST client bound to a single festival's credentials."""

    def __init__(self, *, festival_id: str, refresh_token: str,
                 client_id: str = "", client_secret: str = ""):
        self._festival_id   = festival_id
        self._refresh_token = refresh_token
        self._client_id     = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        # poll cadence — short enough to feel snappy, long enough not to
        # hammer Canva during autofill jobs (typical: 2-4 seconds)
        self._poll_interval = 2.0
        self._poll_timeout  = 180.0

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    async def for_festival(cls, festival_id: str) -> "CanvaClient":
        if not ObjectId.is_valid(festival_id):
            raise CanvaAuthError(f"Invalid festival_id {festival_id!r}")

        festival = await MongoDB.festivals().find_one({"_id": ObjectId(festival_id)})
        if not festival:
            raise CanvaAuthError(f"Festival {festival_id} not found")

        # ── Refresh token (on the festival doc) ──────────────────────────────
        canva = festival.get("canva") or {}
        enc = canva.get("refresh_token_enc", "")
        if not enc:
            raise CanvaAuthError(
                "Canva is not connected for this festival. "
                "Ask the festival user to click Connect Canva."
            )
        try:
            refresh_token = decrypt(enc)
        except ValueError as exc:
            raise CanvaAuthError(
                f"Stored Canva refresh token can't be decrypted ({exc}). "
                "ENCRYPTION_KEY may have rotated — reconnect Canva."
            )

        # ── client_id / client_secret: from the admin who owns this festival ─
        admin_id = festival.get("created_by", "")
        if not admin_id or not ObjectId.is_valid(admin_id):
            raise CanvaAuthError("Festival has no admin owner (created_by missing)")

        admin = await MongoDB.users().find_one({"_id": ObjectId(admin_id)})
        if not admin:
            raise CanvaAuthError(f"Admin user {admin_id} not found")

        admin_canva = admin.get("canva") or {}
        client_id = admin_canva.get("client_id", "")
        try:
            client_secret = decrypt(admin_canva.get("client_secret_enc", ""))
        except ValueError as exc:
            raise CanvaAuthError(f"Admin Canva secret can't be decrypted: {exc}")

        if not client_id or not client_secret:
            raise CanvaAuthError(
                "Admin hasn't configured Canva credentials yet. "
                "Go to Admin → Profile → Canva integration."
            )

        client = cls(
            festival_id=festival_id,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
        await client._refresh_access_token()
        return client

    # ── Token refresh ────────────────────────────────────────────────────────

    async def _refresh_access_token(self) -> None:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                settings.CANVA_TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": self._refresh_token,
                },
            )
        if r.status_code != 200:
            raise CanvaAuthError(
                f"Canva token refresh failed {r.status_code}: {r.text[:200]}"
            )

        body = r.json()
        self._access_token = body["access_token"]

        # Canva may rotate the refresh token; if so, persist the new one.
        new_refresh = body.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            await MongoDB.festivals().update_one(
                {"_id": ObjectId(self._festival_id)},
                {"$set": {
                    "canva.refresh_token_enc": encrypt(new_refresh),
                    "updated_at": datetime.now(timezone.utc),
                }},
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
        }

    # ── HTTP with one-shot 401 retry ────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{get_settings().CANVA_API_BASE}{path}"

        async def _do() -> httpx.Response:
            async with httpx.AsyncClient(timeout=30) as http:
                return await http.request(method, url, headers=self._headers(), json=json)

        r = await _do()
        if r.status_code == 401:
            await self._refresh_access_token()
            r = await _do()

        if not r.is_success:
            raise CanvaApiError(
                f"Canva {method} {path} → {r.status_code}: {r.text[:300]}"
            )
        return r.json()

    # ── Autofill ─────────────────────────────────────────────────────────────

    async def autofill(
        self,
        brand_template_id: str,
        title: str,
        data: dict[str, str],
    ) -> str:
        """
        Submit an autofill job and wait until Canva produces a design.
        `data` maps Canva field name → value (e.g. {"Name": "Renato Santana"}).
        Returns the resulting design_id.
        """
        payload = {
            "brand_template_id": brand_template_id,
            "title":             title,
            "data": {
                key: {"type": "text", "text": str(value)}
                for key, value in data.items()
            },
        }
        body = await self._request("POST", "/autofills", json=payload)
        job_id = body["job"]["id"]
        return await self._poll_autofill(job_id)

    async def _poll_autofill(self, job_id: str) -> str:
        deadline = asyncio.get_event_loop().time() + self._poll_timeout
        while asyncio.get_event_loop().time() < deadline:
            body = await self._request("GET", f"/autofills/{job_id}")
            job = body["job"]
            status = job.get("status")
            if status == "success":
                return job["result"]["design"]["id"]
            if status == "failed":
                raise CanvaApiError(
                    f"Canva autofill failed: {job.get('error', body)}"
                )
            await asyncio.sleep(self._poll_interval)
        raise CanvaApiError(f"Canva autofill timed out after {self._poll_timeout}s")

    # ── Export ───────────────────────────────────────────────────────────────

    async def export_pdf(self, design_id: str) -> str:
        """Export a design as PDF; return the download URL."""
        payload = {
            "design_id": design_id,
            "format":    {"type": "pdf", "export_quality": "regular"},
        }
        body = await self._request("POST", "/exports", json=payload)
        job_id = body["job"]["id"]
        return await self._poll_export(job_id)

    async def _poll_export(self, job_id: str) -> str:
        deadline = asyncio.get_event_loop().time() + self._poll_timeout
        while asyncio.get_event_loop().time() < deadline:
            body = await self._request("GET", f"/exports/{job_id}")
            job = body["job"]
            status = job.get("status")
            if status == "success":
                # Newer responses put urls at job.urls, older at job.result.urls
                urls = job.get("urls") or job.get("result", {}).get("urls", [])
                if not urls:
                    raise CanvaApiError(f"Export succeeded but no URLs: {body}")
                return urls[0]
            if status == "failed":
                raise CanvaApiError(f"Canva export failed: {job.get('error', body)}")
            await asyncio.sleep(self._poll_interval)
        raise CanvaApiError(f"Canva export timed out after {self._poll_timeout}s")

    # ── Download ─────────────────────────────────────────────────────────────

    async def download(self, url: str) -> bytes:
        """Fetch the exported PDF and return its bytes. Canva download URLs
        are pre-signed and don't need an Authorization header."""
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.get(url)
        if not r.is_success:
            raise CanvaApiError(f"Download failed {r.status_code}: {r.text[:200]}")
        return r.content
