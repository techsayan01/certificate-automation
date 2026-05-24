"""
Generates the full Award Winner email header image:
  - Dark navy gradient background
  - Scattered coloured confetti pieces
  - "GLOBAL VISIONARIES FILM FESTIVAL" label
  - Trophy symbol
  - Gold "AWARD WINNER" badge

Run whenever you want to refresh:
    python utils/generate_confetti.py

Output: projects/cinema_na_santa/assets/confetti_header.png
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont

W, H = 640, 220

CONFETTI_COLORS = [
    "#FFD700", "#FFD700", "#FFD700",
    "#FF6B6B", "#FF8A65",
    "#4FC3F7", "#29B6F6",
    "#A8E063", "#66BB6A",
    "#CE93D8", "#AB47BC",
    "#FFFFFF",
]

FONT_PATHS = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/ArialHB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rotated_rect(draw, cx, cy, w, h, angle_deg, color):
    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    hw, hh = w / 2, h / 2
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    pts = [(cx + x*cos_a - y*sin_a, cy + x*sin_a + y*cos_a) for x, y in corners]
    draw.polygon(pts, fill=color)


def generate(output_path: str):
    random.seed(42)
    img  = Image.new("RGB", (W, H), (10, 10, 26))
    draw = ImageDraw.Draw(img)

    # ── gradient background ───────────────────────────────────────────────────
    for y in range(H):
        t = y / H
        r = int(10 + 12 * t)
        g = int(10 + 23 * t)
        b = int(26 + 36 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # ── confetti ──────────────────────────────────────────────────────────────
    for _ in range(130):
        x     = random.randint(-10, W + 10)
        y     = random.randint(-10, H + 10)
        color = _hex(random.choice(CONFETTI_COLORS))
        shape = random.choice(["rect", "rect", "circle", "ribbon"])
        angle = random.randint(0, 360)

        if shape == "rect":
            _rotated_rect(draw, x, y, random.randint(5, 14), random.randint(4, 10), angle, color)
        elif shape == "circle":
            r = random.randint(3, 7)
            draw.ellipse([x-r, y-r, x+r, y+r], fill=color)
        elif shape == "ribbon":
            _rotated_rect(draw, x, y, random.randint(3, 6), random.randint(12, 22), angle, color)

    # ── dark vignette so text stays readable ──────────────────────────────────
    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    for y in range(H):
        alpha = int(160 * (y / H) ** 1.5)
        vd.line([(0, y), (W, y)], fill=(8, 8, 20, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── "GLOBAL VISIONARIES FILM FESTIVAL" label ──────────────────────────────
    lbl_font = _font(11)
    label    = "GLOBAL VISIONARIES FILM FESTIVAL"
    # letter-spaced manually
    spaced   = "  ".join(label)
    bb       = draw.textbbox((0, 0), spaced, font=lbl_font)
    lx       = (W - (bb[2] - bb[0])) // 2
    draw.text((lx, 52), spaced, font=lbl_font, fill=(212, 175, 55))

    # ── trophy text ───────────────────────────────────────────────────────────
    trophy_font = _font(42)
    trophy_text = "★"
    tb = draw.textbbox((0, 0), trophy_text, font=trophy_font)
    tx = (W - (tb[2] - tb[0])) // 2
    draw.text((tx, 78), trophy_text, font=trophy_font, fill=(212, 175, 55))

    # ── gold "AWARD WINNER" badge ─────────────────────────────────────────────
    badge_font  = _font(15)
    badge_text  = "  ✦  AWARD WINNER  ✦  "
    bb2         = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw          = bb2[2] - bb2[0] + 24
    bh          = bb2[3] - bb2[1] + 14
    bx          = (W - bw) // 2
    by          = 158

    # gold gradient fill for badge
    badge_img = Image.new("RGB", (bw, bh), (212, 175, 55))
    bd = ImageDraw.Draw(badge_img)
    for gx in range(bw):
        t = gx / bw
        r = int(212 + (245 - 212) * t)
        g = int(175 + (226 - 175) * t)
        b = int(55  + (122 - 55)  * t)
        bd.line([(gx, 0), (gx, bh)], fill=(r, g, b))

    # rounded corners mask
    mask = Image.new("L", (bw, bh), 0)
    md   = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, bw-1, bh-1], radius=bh//2, fill=255)

    img.paste(badge_img, (bx, by), mask)

    # badge text
    draw.text((bx + 12, by + 7), badge_text, font=badge_font, fill=(10, 10, 26))

    # ── gold bottom border line ───────────────────────────────────────────────
    draw.line([(0, H-2), (W, H-2)], fill=(212, 175, 55), width=2)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG", optimize=True)
    size_kb = out.stat().st_size // 1024
    print(f"Header image saved → {out}  ({size_kb} KB)")
    return str(out)


if __name__ == "__main__":
    path = generate("projects/cinema_na_santa/assets/confetti_header.png")
    img  = Image.open(path)
    img.show()
