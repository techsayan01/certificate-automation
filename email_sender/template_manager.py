"""
TemplateManager — two-tier template resolution.

Lookup order for a given category:
  Tier 1 — project templates   (projects/<name>/templates/)
    1a. Explicit entry in projects/<name>/templates/category_map.json
    1b. Derived filename  e.g. "Best Short Film" → best_short_film.html
    1c. projects/<name>/templates/default.html

  Tier 2 — global fallback     (email_sender/templates/)
    2a. Explicit entry in email_sender/templates/category_map.json
    2b. Derived filename
    2c. email_sender/templates/default.html

This means each festival project can have its own full set of branded
templates, while categories not covered fall back to the shared set.

All templates are rendered with Jinja2. Every key in the recipient dict
plus `pdf_filename` is available as a template variable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


def _slug(category: str) -> str:
    """'Best Short Film (Main Category)' → 'best_short_film_main_category.html'"""
    return re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_") + ".html"


def _load_map(directory: Path) -> dict[str, str]:
    path = directory / "category_map.json"
    if path.exists():
        with open(path) as fh:
            return json.load(fh)
    return {}


class TemplateManager:
    def __init__(self):
        self._project_dir  = Path(Config.PROJECT_TEMPLATE_DIR)
        self._global_dir   = Path(Config.GLOBAL_TEMPLATE_DIR)
        self._default      = Config.DEFAULT_TEMPLATE

        # Build a combined Jinja2 loader: project templates override global ones
        search_paths = []
        if self._project_dir.exists():
            search_paths.append(str(self._project_dir))
        search_paths.append(str(self._global_dir))

        self._env = Environment(
            loader=FileSystemLoader(search_paths),
            autoescape=True,
        )

        self._project_map = _load_map(self._project_dir)
        self._global_map  = _load_map(self._global_dir)

        logger.debug(
            f"TemplateManager ready | "
            f"project_dir={self._project_dir} | "
            f"global_dir={self._global_dir}"
        )

    # ── public ────────────────────────────────────────────────────────────────

    def render(self, category: str, context: dict) -> str:
        """Render and return the HTML body for the given category."""
        template_name = self._resolve(category)
        logger.debug(f"  Template: {template_name}  (category: '{category}')")
        return self._env.get_template(template_name).render(**context)

    # ── private ───────────────────────────────────────────────────────────────

    def _resolve(self, category: str) -> str:
        """
        Walk the two-tier lookup chain and return the first template
        filename that actually exists.
        """
        candidates = [
            # Tier 1 — project
            self._project_map.get(category),          # 1a explicit map
            _slug(category),                           # 1b derived (Jinja2 searches project dir first)
            # Tier 2 — global fallback (same slug, but Jinja2 will find it in global dir)
            self._global_map.get(category),            # 2a explicit global map
            self._default,                             # final fallback
        ]

        for name in candidates:
            if not name:
                continue
            try:
                self._env.get_template(name)
                return name
            except TemplateNotFound:
                continue

        # Should never reach here because default.html always exists globally
        raise FileNotFoundError(
            f"No template found for category '{category}' and default.html is missing."
        )
