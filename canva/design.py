"""
CertificateGenerator — renders certificates from a local PNG/JPG template
using Pillow. No Canva API or credentials required.

How to get your template image:
  1. Open your certificate in Canva.
  2. File → Download → PNG  (choose the blank version — no name/text filled in).
  3. Save it as  data/certificate_template.png  (or set CERT_TEMPLATE_PATH in .env).

Text placement:
  Each field (Name, Project, Category) has an (x, y) coordinate and a font size
  defined in .env. Run  python canva/preview.py  to see a labelled preview image
  that shows the pixel coordinates of every point — use that to dial in your values.

Single-line guarantee:
  If the rendered text is wider than MAX_WIDTH_PX, the font size is reduced by 1pt
  at a time until it fits.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    # Fall back to Pillow's built-in bitmap font (always available)
    logger.warning(
        f"Font not found at '{path}' — using default bitmap font. "
        "Set CERT_FONT_PATH in .env for a proper TTF/OTF font."
    )
    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    font_size: int,
    max_width: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Reduce font size until the text fits within max_width pixels."""
    font = _load_font(font_path, font_size)
    while font_size > 8:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        if text_width <= max_width:
            break
        font_size -= 1
        font = _load_font(font_path, font_size)
    return font


# ─── main class ───────────────────────────────────────────────────────────────

class CanvaDesignManager:           # name kept so main.py needs no changes
    """Generates certificate PDFs by compositing text onto a PNG template."""

    def generate_certificate(
        self,
        name: str,
        project: str,
        category: str,
        output_path: str,
    ) -> str:
        template_path = Config.CERT_TEMPLATE_PATH
        if not os.path.exists(template_path):
            raise FileNotFoundError(
                f"Certificate template not found: {template_path}\n"
                "Export your blank Canva design as PNG and set CERT_TEMPLATE_PATH in .env."
            )

        img = Image.open(template_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        fields = [
            (
                Config.CERT_NAME_FIELD,
                name,
                Config.CERT_NAME_X,
                Config.CERT_NAME_Y,
                Config.CERT_NAME_FONT_SIZE,
                Config.CERT_NAME_COLOR,
                Config.CERT_NAME_ANCHOR,
            ),
            (
                Config.CERT_PROJECT_FIELD,
                project,
                Config.CERT_PROJECT_X,
                Config.CERT_PROJECT_Y,
                Config.CERT_PROJECT_FONT_SIZE,
                Config.CERT_PROJECT_COLOR,
                Config.CERT_PROJECT_ANCHOR,
            ),
            (
                Config.CERT_CATEGORY_FIELD,
                category,
                Config.CERT_CATEGORY_X,
                Config.CERT_CATEGORY_Y,
                Config.CERT_CATEGORY_FONT_SIZE,
                Config.CERT_CATEGORY_COLOR,
                Config.CERT_CATEGORY_ANCHOR,
            ),
        ]

        for field_name, text, x, y, font_size, color, anchor in fields:
            font = _fit_text(
                draw, text,
                font_path=Config.CERT_FONT_PATH,
                font_size=font_size,
                max_width=Config.CERT_MAX_TEXT_WIDTH,
            )
            draw.text((x, y), text, font=font, fill=color, anchor=anchor)
            logger.debug(f"  Drew '{field_name}': \"{text}\" at ({x}, {y})")

        # Save as PDF (Pillow converts PNG→PDF automatically)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        rgb = img.convert("RGB")
        rgb.save(output_path, "PDF", resolution=150)
        return output_path
