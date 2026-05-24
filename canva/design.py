"""
CanvaDesignManager — end-to-end certificate generation.

Steps per recipient:
  1. POST /autofills       → start autofill job (brand template + field values)
  2. GET  /autofills/{id}  → poll until success → get new design_id
  3. POST /exports         → start PDF export job
  4. GET  /exports/{id}    → poll until success → get download URL
  5. Stream download → save to output_path

Single-line constraint:
  Canva's autofill API does not expose font-size directly, so we truncate
  names/values that exceed MAX_CHARS to keep them on one line visually.
  For finer control, adjust your Canva template's text-box to use
  "auto-resize" (shrink text to fit) inside the editor.
"""

import time
import urllib.parse
from pathlib import Path

import requests

from canva.client import CanvaClient
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

# Characters beyond this limit get truncated with "…" to prevent visual wrapping.
# Tune to match the width of your certificate text boxes.
MAX_CHARS = {
    "name": 40,
    "project": 60,
    "category": 50,
}


def _truncate(value: str, field: str) -> str:
    limit = MAX_CHARS.get(field, 60)
    if len(value) <= limit:
        return value
    logger.warning(
        f"  '{field}' value is {len(value)} chars (limit {limit}) — truncating."
    )
    return value[: limit - 1] + "…"


class CanvaDesignManager:
    def __init__(self):
        self._client = CanvaClient()

    # ── public ────────────────────────────────────────────────────────────────

    def generate_certificate(
        self,
        name: str,
        project: str,
        category: str,
        output_path: str,
    ) -> str:
        """
        Full pipeline: autofill → export → download.
        Returns the saved output_path.
        """
        design_id = self._autofill(name, project, category)
        download_url = self._export(design_id)
        self._download(download_url, output_path)
        return output_path

    # ── step 1: autofill ──────────────────────────────────────────────────────

    def _autofill(self, name: str, project: str, category: str) -> str:
        """
        Trigger an autofill job on the brand template and return the
        resulting design_id once the job succeeds.
        """
        payload = {
            "brand_template_id": Config.CANVA_BRAND_TEMPLATE_ID,
            "title": f"Certificate — {name}",
            "data": {
                Config.CANVA_NAME_FIELD: {
                    "type": "text",
                    "text": _truncate(name, "name"),
                },
                Config.CANVA_PROJECT_FIELD: {
                    "type": "text",
                    "text": _truncate(project, "project"),
                },
                Config.CANVA_CATEGORY_FIELD: {
                    "type": "text",
                    "text": _truncate(category, "category"),
                },
            },
        }

        response = self._client.post("/autofills", payload)
        job_id = response["job"]["id"]
        logger.debug(f"  Autofill job started: {job_id}")

        return self._poll_autofill(job_id)

    def _poll_autofill(self, job_id: str) -> str:
        deadline = time.time() + Config.CANVA_POLL_TIMEOUT
        while time.time() < deadline:
            data = self._client.get(f"/autofills/{job_id}")
            job = data["job"]
            status = job["status"]

            if status == "success":
                design_id = job["result"]["design"]["id"]
                logger.debug(f"  Autofill complete → design_id: {design_id}")
                return design_id

            if status == "failed":
                raise RuntimeError(
                    f"Canva autofill job {job_id} failed: {job.get('error')}"
                )

            time.sleep(Config.CANVA_POLL_INTERVAL)

        raise TimeoutError(
            f"Canva autofill job {job_id} did not finish within "
            f"{Config.CANVA_POLL_TIMEOUT}s."
        )

    # ── step 2: export ────────────────────────────────────────────────────────

    def _export(self, design_id: str) -> str:
        """Kick off a PDF export and return the download URL."""
        payload = {
            "design_id": design_id,
            "format": {"type": "pdf"},
        }
        response = self._client.post("/exports", payload)
        export_id = response["job"]["id"]
        logger.debug(f"  Export job started: {export_id}")

        return self._poll_export(export_id)

    def _poll_export(self, export_id: str) -> str:
        deadline = time.time() + Config.CANVA_POLL_TIMEOUT
        while time.time() < deadline:
            data = self._client.get(f"/exports/{export_id}")
            job = data["job"]
            status = job["status"]

            if status == "success":
                url = job["urls"][0]
                logger.debug(f"  Export ready: {url[:60]}…")
                return url

            if status == "failed":
                raise RuntimeError(
                    f"Canva export job {export_id} failed: {job.get('error')}"
                )

            time.sleep(Config.CANVA_POLL_INTERVAL)

        raise TimeoutError(
            f"Canva export job {export_id} did not finish within "
            f"{Config.CANVA_POLL_TIMEOUT}s."
        )

    # ── step 3: download ──────────────────────────────────────────────────────

    @staticmethod
    def _download(url: str, output_path: str):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(output_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8192):
                    fh.write(chunk)
        logger.debug(f"  PDF saved → {output_path}")
