"""
CanvaDesignManager — generates certificate PDFs via the Canva Autofill API.

Template resolution per category (Brand Template ID):
  1. projects/<name>/templates/canva_templates.json  → category-specific template
  2. CANVA_BRAND_TEMPLATE_ID in projects/<name>/.env → festival-wide default

Flow per recipient:
  1. POST /autofills  — fills Name / Project / Category into the brand template
  2. Poll until the new design is ready
  3. POST /exports    — exports as PDF
  4. Poll + download  → saves the PDF to output_path

Field names must match the placeholders you defined in Canva.
Defaults: CANVA_NAME_FIELD="Name", CANVA_PROJECT_FIELD="Project",
          CANVA_CATEGORY_FIELD="Category"
Override these in .env if your Canva field names differ.
"""

from __future__ import annotations

import json
from pathlib import Path

from canva.client import CanvaClient
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


def _load_template_map() -> dict[str, str]:
    """Load projects/<name>/templates/canva_templates.json if it exists."""
    path = Path(Config.PROJECT_TEMPLATE_DIR) / "canva_templates.json"
    if not path.exists():
        return {}
    with open(path) as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _resolve_template_id(category: str) -> str:
    """
    Return the Brand Template ID for a given category.
    Falls back to the project/global default if no category-specific entry.
    """
    template_map = _load_template_map()
    if category in template_map:
        tid = template_map[category]
        logger.debug(f"  Category template: {category} → {tid}")
        return tid
    tid = Config.CANVA_BRAND_TEMPLATE_ID
    if not tid:
        raise ValueError(
            "No Canva Brand Template ID configured. "
            f"Set CANVA_BRAND_TEMPLATE_ID in projects/{Config.PROJECT}/.env"
        )
    logger.debug(f"  Default template for '{category}': {tid}")
    return tid


class CanvaDesignManager:
    def __init__(self):
        self._client = CanvaClient()

    def generate_certificate(
        self,
        name: str,
        project: str,
        category: str,
        output_path: str,
    ) -> str:
        """
        Generate a personalised certificate PDF for one recipient via Canva API.
        Returns output_path.
        """
        brand_template_id = _resolve_template_id(category)
        title = "Certificate_" + name.replace(" ", "_")

        # Build data payload using configured field names
        data = {
            Config.CANVA_NAME_FIELD:     name,
            Config.CANVA_PROJECT_FIELD:  project,
            Config.CANVA_CATEGORY_FIELD: category,
        }

        # Add season fields if configured and the template has those fields
        if Config.CERT_SEASON_TEXT and Config.CANVA_SEASON_FIELD:
            data[Config.CANVA_SEASON_FIELD] = Config.CERT_SEASON_TEXT
        if Config.CERT_SEASON_DATE_TEXT and Config.CANVA_SEASON_DATE_FIELD:
            data[Config.CANVA_SEASON_DATE_FIELD] = Config.CERT_SEASON_DATE_TEXT

        logger.info(f"  Template: {brand_template_id} | Autofilling → {title}")
        logger.debug(f"  Fields: {list(data.keys())}")

        # Step 1 & 2 — autofill → get design_id
        design_id = self._client.autofill_template(
            brand_template_id=brand_template_id,
            title=title,
            data=data,
        )
        logger.info(f"  Design ready: {design_id}")

        # Step 3 & 4 — export as PDF → download
        download_url = self._client.export_design(design_id)
        self._client.download(download_url, output_path)
        logger.info(f"  Certificate saved: {output_path}")

        return output_path
