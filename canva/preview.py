"""
Certificate position calibration tool.

Draws a coordinate grid + current field positions on the template so you can
tune CERT_NAME_X / CERT_NAME_Y etc. in .env until the markers sit exactly
where the text should appear.

Usage:
    python canva/preview.py --project GVFF

Output: data/output/cert_preview.png   (also opens automatically)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont
from config import Config


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/ArialHB.ttc",
        "/Library/Fonts/Arial Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project",  required=True)
    parser.add_argument("--template", default=None,
                        help="Template PNG filename (without path), e.g. certificate_finalist.png. "
                             "Defaults to CERT_TEMPLATE_PATH in .env.")
    args = parser.parse_args()

    Config.load(args.project)

    if args.template:
        template = f"projects/{args.project}/assets/{args.template}"
    else:
        template = Config.CERT_TEMPLATE_PATH

    if not template or not Path(template).exists():
        print(f"\n[ERROR] Template not found: {template}")
        print(f"Export your blank certificate from Canva as PNG and place it in:")
        print(f"  projects/{args.project}/assets/")
        print(f"Then pass it with: --template certificate_award_winner.png\n")
        sys.exit(1)

    img  = Image.open(template).convert("RGBA")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    print(f"\nTemplate : {template}")
    print(f"Size     : {W} × {H} px")

    # ── red coordinate grid every 100px ──────────────────────────────────────
    small = _font(16)
    step  = 100
    for x in range(0, W, step):
        draw.line([(x, 0), (x, H)], fill=(220, 0, 0, 60), width=1)
    for y in range(0, H, step):
        draw.line([(0, y), (W, y)], fill=(220, 0, 0, 60), width=1)
    for x in range(0, W, step):
        for y in range(0, H, step):
            draw.text((x + 3, y + 3), f"{x},{y}", font=small, fill=(220, 0, 0, 180))

    # ── green markers for current field positions ─────────────────────────────
    fields = [
        ("NAME",     Config.CERT_NAME_X,     Config.CERT_NAME_Y,
         Config.CERT_NAME_MAX_WIDTH,
         f"← Name ({Config.CERT_NAME_X},{Config.CERT_NAME_Y}) "
         f"max_w={Config.CERT_NAME_MAX_WIDTH} sz={Config.CERT_NAME_FONT_SIZE}"),
        ("PROJECT",  Config.CERT_PROJECT_X,  Config.CERT_PROJECT_Y,
         Config.CERT_PROJECT_MAX_WIDTH,
         f"← Project ({Config.CERT_PROJECT_X},{Config.CERT_PROJECT_Y}) "
         f"max_w={Config.CERT_PROJECT_MAX_WIDTH} sz={Config.CERT_PROJECT_FONT_SIZE}"),
        ("CATEGORY", Config.CERT_CATEGORY_X, Config.CERT_CATEGORY_Y,
         Config.CERT_CATEGORY_MAX_WIDTH,
         f"← Category ({Config.CERT_CATEGORY_X},{Config.CERT_CATEGORY_Y}) "
         f"max_w={Config.CERT_CATEGORY_MAX_WIDTH} sz={Config.CERT_CATEGORY_FONT_SIZE}"),
        ("SEASON",   Config.CERT_SEASON_X,   Config.CERT_SEASON_Y,
         Config.CERT_SEASON_MAX_WIDTH,
         f"← Season ({Config.CERT_SEASON_X},{Config.CERT_SEASON_Y}) "
         f"max_w={Config.CERT_SEASON_MAX_WIDTH} sz={Config.CERT_SEASON_FONT_SIZE}"),
        ("SEASON DATE", Config.CERT_SEASON_DATE_X, Config.CERT_SEASON_DATE_Y,
         Config.CERT_SEASON_DATE_MAX_WIDTH,
         f"← Season date ({Config.CERT_SEASON_DATE_X},{Config.CERT_SEASON_DATE_Y}) "
         f"max_w={Config.CERT_SEASON_DATE_MAX_WIDTH} sz={Config.CERT_SEASON_DATE_FONT_SIZE}"),
    ]

    lbl = _font(18)
    for label, x, y, mw, info in fields:
        if x == 0 and y == 0:
            continue   # skip unconfigured fields
        draw.ellipse([x-8, y-8, x+8, y+8], fill=(0, 220, 0, 220))
        draw.line([(x, y), (x + mw, y)], fill=(0, 220, 0, 120), width=2)
        draw.text((x + 12, y - 22), info, font=lbl, fill=(0, 220, 0, 240))

    # ── save & open ───────────────────────────────────────────────────────────
    out = Path("data/output/cert_preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(str(out))
    print(f"\nPreview saved → {out}")
    print("\nCurrent field positions:")
    for label, x, y, _, __ in fields:
        print(f"  {label:10s}: ({x}, {y})")
    print()
    print("Adjust CERT_NAME_X/Y, CERT_PROJECT_X/Y, CERT_CATEGORY_X/Y in")
    print(f"projects/{args.project}/.env until the green dots sit correctly.")
    print("Re-run this script after each change to see the updated positions.\n")

    try:
        img.show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
