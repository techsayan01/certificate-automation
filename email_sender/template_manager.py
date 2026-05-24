"""
TemplateManager — loads the right HTML email template for a given category.

Lookup order:
  1. Check category_map.json for an explicit category → filename mapping.
  2. Try a filename derived from the category string
     (e.g. "Best Student Film" → best_student_film.html).
  3. Fall back to default.html.

All templates are rendered with Jinja2; every key in the recipient dict
is available as a template variable.
"""

import json
import os
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


def _category_to_filename(category: str) -> str:
    """'Best Student Film (Main Category)' → 'best_student_film_main_category.html'"""
    slug = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    return f"{slug}.html"


class TemplateManager:
    def __init__(self, template_dir: str):
        self._dir = Path(template_dir)
        self._env = Environment(
            loader=FileSystemLoader(str(self._dir)),
            autoescape=True,
        )
        self._map: dict[str, str] = self._load_map()

    # ── public ────────────────────────────────────────────────────────────────

    def render(self, category: str, context: dict) -> str:
        """Return a rendered HTML string for the given category and context."""
        template_name = self._resolve(category)
        logger.debug(f"  Using email template: {template_name}")
        tpl = self._env.get_template(template_name)
        return tpl.render(**context)

    # ── private ───────────────────────────────────────────────────────────────

    def _load_map(self) -> dict[str, str]:
        map_path = self._dir / "category_map.json"
        if map_path.exists():
            with open(map_path) as fh:
                return json.load(fh)
        return {}

    def _resolve(self, category: str) -> str:
        # 1. explicit map
        if category in self._map:
            candidate = self._map[category]
            if (self._dir / candidate).exists():
                return candidate

        # 2. derived filename
        candidate = _category_to_filename(category)
        try:
            self._env.get_template(candidate)
            return candidate
        except TemplateNotFound:
            pass

        # 3. default
        return Config.DEFAULT_TEMPLATE
