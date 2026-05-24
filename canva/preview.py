"""
Coordinate preview tool — run this once to figure out where to place
Name / Project / Category text on your certificate template.

Usage:
    python canva/preview.py

Opens (or saves) an annotated version of your certificate template with:
  - A red crosshair grid every 100px
  - Pixel coordinates labelled at each grid intersection
  - The current Name / Project / Category positions drawn in green

Adjust CERT_NAME_X, CERT_NAME_Y etc. in .env until the green labels
sit exactly where you want the text to appear, then run the full pipeline.
"""

import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont
from config import Config


def main():
    template = Config.CERT_TEMPLATE_PATH
    try:
        img = Image.open(template).convert("RGBA")
    except FileNotFoundError:
        print(f"[ERROR] Template not found: {template}")
        print("Export your blank Canva certificate as PNG and set CERT_TEMPLATE_PATH in .env")
        sys.exit(1)

    draw = ImageDraw.Draw(img)
    w, h = img.size

    # ── grid ──────────────────────────────────────────────────────────────────
    try:
        small = ImageFont.truetype(Config.CERT_FONT_PATH, 14)
    except Exception:
        small = ImageFont.load_default()

    step = 100
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=(200, 0, 0, 80), width=1)
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=(200, 0, 0, 80), width=1)
    for x in range(0, w, step):
        for y in range(0, h, step):
            draw.text((x + 2, y + 2), f"{x},{y}", fill=(200, 0, 0, 200), font=small)

    # ── current field positions ───────────────────────────────────────────────
    fields = [
        ("NAME",     Config.CERT_NAME_X,     Config.CERT_NAME_Y,     "Alice Johnson"),
        ("PROJECT",  Config.CERT_PROJECT_X,  Config.CERT_PROJECT_Y,  "Hunting Fireflies"),
        ("CATEGORY", Config.CERT_CATEGORY_X, Config.CERT_CATEGORY_Y, "Best Student Film"),
    ]
    for label, x, y, sample in fields:
        try:
            font = ImageFont.truetype(Config.CERT_FONT_PATH, 22)
        except Exception:
            font = ImageFont.load_default()
        draw.text((x, y), sample, font=font, fill=(0, 180, 0, 255),
                  anchor=Config.CERT_NAME_ANCHOR)
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(0, 200, 0, 255))
        draw.text((x + 10, y - 20), f"← {label} ({x},{y})",
                  font=small, fill=(0, 150, 0, 255))

    # ── save / open ───────────────────────────────────────────────────────────
    out = Path("data/output/preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out)
    print(f"Preview saved → {out}")
    print(f"Image size: {w} x {h} px")
    print()
    print("Current field positions (from .env):")
    for label, x, y, _ in fields:
        print(f"  {label:10s}: ({x}, {y})")
    print()
    print("Edit CERT_NAME_X / CERT_NAME_Y etc. in .env until the green labels sit")
    print("exactly where you want the text, then run: python main.py --dry-run")

    try:
        img.show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
