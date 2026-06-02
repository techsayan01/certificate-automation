"""
Canva REST API client.

Handles autofill, export, and download — the three-step flow for
generating a filled certificate PDF from a brand template.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from canva.auth import get_canva_token
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


class CanvaClient:
    def __init__(self):
        self._token = get_canva_token()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    def _get(self, path: str) -> dict:
        r = requests.get(
            f"{Config.CANVA_API_BASE}{path}",
            headers=self._headers(),
        )
        if not r.ok:
            raise RuntimeError(f"Canva GET {path} → {r.status_code}: {r.text[:300]}")
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(
            f"{Config.CANVA_API_BASE}{path}",
            headers=self._headers(),
            json=body,
        )
        if not r.ok:
            raise RuntimeError(f"Canva POST {path} → {r.status_code}: {r.text[:300]}")
        return r.json()

    # ── Autofill ──────────────────────────────────────────────────────────────

    def autofill_template(
        self,
        brand_template_id: str,
        title: str,
        data: dict[str, str],
    ) -> str:
        """
        Submit an autofill job and wait for it to complete.
        data = {"Name": "Alice Johnson", "Project": "...", "Category": "..."}
        Returns the resulting design_id.
        """
        payload = {
            "brand_template_id": brand_template_id,
            "title": title,
            "data": {
                key: {"type": "text", "text": value}
                for key, value in data.items()
            },
        }
        resp   = self._post("/autofills", payload)
        job_id = resp["job"]["id"]
        logger.debug(f"Autofill job created: {job_id}")
        return self._poll_autofill(job_id)

    def _poll_autofill(self, job_id: str) -> str:
        deadline = time.time() + Config.CANVA_POLL_TIMEOUT
        while time.time() < deadline:
            resp   = self._get(f"/autofills/{job_id}")
            status = resp["job"]["status"]
            if status == "success":
                design_id = resp["job"]["result"]["design"]["id"]
                logger.debug(f"Autofill done → design_id: {design_id}")
                return design_id
            if status == "failed":
                raise RuntimeError(
                    f"Canva autofill failed: {resp['job'].get('error', resp)}"
                )
            time.sleep(Config.CANVA_POLL_INTERVAL)
        raise TimeoutError(
            f"Canva autofill timed out after {Config.CANVA_POLL_TIMEOUT}s"
        )

    # ── Export ────────────────────────────────────────────────────────────────

    def export_design(self, design_id: str) -> str:
        """
        Export a design as PDF. Returns the download URL.
        """
        payload = {
            "design_id": design_id,
            "format":    {"type": "pdf", "export_quality": "regular"},
        }
        resp   = self._post("/exports", payload)
        job_id = resp["job"]["id"]
        logger.debug(f"Export job created: {job_id}")
        return self._poll_export(job_id)

    def _poll_export(self, job_id: str) -> str:
        deadline = time.time() + Config.CANVA_POLL_TIMEOUT
        while time.time() < deadline:
            resp   = self._get(f"/exports/{job_id}")
            status = resp["job"]["status"]
            if status == "success":
                # Canva returns a list of URLs; take the first
                urls = resp["job"].get("urls") or resp["job"].get("result", {}).get("urls", [])
                if not urls:
                    raise RuntimeError(f"Export succeeded but no URLs in response: {resp}")
                url = urls[0]
                logger.debug(f"Export done → {url}")
                return url
            if status == "failed":
                raise RuntimeError(
                    f"Canva export failed: {resp['job'].get('error', resp)}"
                )
            time.sleep(Config.CANVA_POLL_INTERVAL)
        raise TimeoutError(
            f"Canva export timed out after {Config.CANVA_POLL_TIMEOUT}s"
        )

    # ── Download ──────────────────────────────────────────────────────────────

    def download(self, url: str, output_path: str) -> None:
        """Stream-download a file from url → output_path."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(output_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8192):
                    fh.write(chunk)
        logger.debug(f"Downloaded → {output_path}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def list_template_fields(self, brand_template_id: str) -> list[str]:
        """
        Return the placeholder field names defined in a brand template.
        Run  python canva/list_fields.py  to see yours.
        """
        resp   = self._get(f"/brand-templates/{brand_template_id}/dataset")
        fields = resp.get("dataset", {})
        return list(fields.keys())
