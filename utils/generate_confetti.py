"""
Generates a static confetti header PNG for the Award Winner email
and updates the template with the base64-encoded image.

Run once (or whenever you want a fresh confetti layout):
    python utils/generate_confetti.py

Output: projects/cinema_na_santa/assets/confetti_header.png
"""

import base64
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

W, H   = 640, 220
COLORS = [
    "#FFD700", "#FFD700", "#FFD700",
    "#FF6B6B", "#FF8A65",
    "#4FC3F7", "#29B6F6",
    "#A8E063", "#66BB6A",
    "#CE93D8", "#AB47BC",
    "#FFFFFF",
]
BG     = (10, 10, 26)
PIECES = 120


def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rotated_rect(draw, cx, cy, w, h, angle_deg, color):
    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    hw, hh = w / 2, h / 2
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    pts = [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a) for x, y in corners]
    draw.polygon(pts, fill=color)


def generate(output_path: str) -> str:
    random.seed(42)
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    for _ in range(PIECES):
        x     = random.randint(-10, W + 10)
        y     = random.randint(-10, H + 10)
        color = _hex(random.choice(COLORS))
        shape = random.choice(["rect", "rect", "circle", "ribbon"])
        angle = random.randint(0, 360)

        if shape == "rect":
            _rotated_rect(draw, x, y, random.randint(5, 14), random.randint(4, 10), angle, color)
        elif shape == "circle":
            r = random.randint(3, 7)
            draw.ellipse([x-r, y-r, x+r, y+r], fill=color)
        elif shape == "ribbon":
            _rotated_rect(draw, x, y, random.randint(3, 6), random.randint(12, 22), angle, color)

    # Dark gradient overlay so bottom text stays readable
    for row in range(H):
        alpha   = int(180 * (row / H) ** 2)
        overlay = Image.new("RGBA", (W, 1), (10, 10, 26, alpha))
        img.paste(overlay, (0, row), overlay.convert("RGBA"))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG", optimize=True)
    print(f"Saved → {out}  ({out.stat().st_size} bytes)")
    return str(out)


if __name__ == "__main__":
    path = generate("projects/cinema_na_santa/assets/confetti_header.png")
    # Print base64 size so you know the embed size
    data = Path(path).read_bytes()
    b64  = base64.b64encode(data).decode()
    print(f"Base64 length: {len(b64)} chars (~{len(b64)//1024} KB in email)")
