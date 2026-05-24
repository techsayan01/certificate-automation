"""
AttachmentManager — loads projects/<name>/templates/attachments.json
and returns the list of extra files to attach for a given category.

attachments.json format:
{
  "Award Winner":   ["assets/winner_laurel_2026.png"],
  "Best Short Film": ["assets/winner_laurel_2026.png"]
}

Paths are relative to the project root (projects/<name>/).
"""

from __future__ import annotations

import json
from pathlib import Path

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


class AttachmentManager:
    def __init__(self):
        self._map: dict[str, list[str]] = {}
        map_path = Path(Config.PROJECT_TEMPLATE_DIR) / "attachments.json"
        if map_path.exists():
            with open(map_path) as fh:
                self._map = json.load(fh)
            logger.debug(f"Loaded attachments map: {len(self._map)} categories")
        else:
            logger.debug("No attachments.json found — no extra attachments will be sent.")

    def get(self, category: str) -> list[str]:
        """Return list of extra attachment paths for the given category."""
        return self._map.get(category, [])
