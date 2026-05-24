"""
Thin HTTP wrapper around the Canva Connect REST API.
Injects the Bearer token on every request and raises on non-2xx responses.
"""

import requests

from canva.auth import CanvaTokenManager
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


class CanvaClient:
    def __init__(self):
        self._auth = CanvaTokenManager()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._auth.get_access_token()}",
            "Content-Type": "application/json",
        }

    def post(self, path: str, payload: dict) -> dict:
        url = f"{Config.CANVA_API_BASE}{path}"
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=60)
        self._raise(resp)
        return resp.json()

    def get(self, path: str) -> dict:
        url = f"{Config.CANVA_API_BASE}{path}"
        resp = requests.get(url, headers=self._headers(), timeout=60)
        self._raise(resp)
        return resp.json()

    @staticmethod
    def _raise(resp: requests.Response):
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"Canva API error {resp.status_code}: {detail}"
            )
