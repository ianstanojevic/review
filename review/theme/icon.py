"""REVIEW's own app icon: a small performance radar.

DRAFT's icon is the gold hextech diamond; REVIEW's is its signature visual —
a gold-rimmed octagon (the radar grid) with a teal radar shape inside — in
the same palette, so the two read as one family but never get confused in
the taskbar. Drawn supersampled with PIL, so it is crisp from 16 px (taskbar)
to 256 px (Explorer).
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .tokens import DARK_BG, EDGE, EDGE_HI, GOOD, PANEL2, hex_rgb

# radar values of the inner shape (0..1), clockwise from the top: an uneven,
# "real-looking" profile so it reads as data, not as a second octagon
SHAPE = (0.95, 0.55, 0.88, 0.72, 0.45, 0.82, 0.50, 0.76)


def _oct(cx, cy, r, vals=None):
    n = 8
    out = []
    for i in range(n):
        a = -math.pi / 2 + 2 * math.pi * i / n
        rr = r * (vals[i] if vals else 1.0)
        out.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return out


def icon_image(size: int) -> Image.Image:
    """RGBA icon at ``size`` px."""
    ss = 8 if size <= 32 else 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")
    c = (S - 1) / 2
    R = S * 0.48
    small = size <= 20
    # body: dark navy octagon with a gold rim (thicker at small sizes)
    rim = S * (0.085 if small else 0.06)
    d.polygon(_oct(c, c, R), fill=hex_rgb(EDGE_HI) + (255,))
    d.polygon(_oct(c, c, R - rim), fill=hex_rgb(DARK_BG) + (255,))
    inner = R - rim
    if not small:
        # faint grid: 50 % ring + spokes
        d.polygon(_oct(c, c, inner * 0.55), outline=hex_rgb(EDGE) + (255,),
                  width=max(1, int(S * 0.012)))
        for x, y in _oct(c, c, inner * 0.97):
            d.line([(c, c), (x, y)], fill=hex_rgb(PANEL2) + (255,),
                   width=max(1, int(S * 0.01)))
    # the radar shape: teal fill + bright teal edge
    pts = _oct(c, c, inner * 0.92, SHAPE)
    d.polygon(pts, fill=hex_rgb(GOOD) + (150 if not small else 200,))
    w = max(1, int(S * (0.04 if small else 0.025)))
    d.line(pts + [pts[0]], fill=hex_rgb(GOOD) + (255,), width=w)
    for x, y in pts:                      # round the joints
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2],
                  fill=hex_rgb(GOOD) + (255,))
    if size >= 32:
        # gold vertex markers on the shape (like the in-app radar)
        r = S * 0.028
        for x, y in pts:
            d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)],
                      fill=hex_rgb(EDGE_HI) + (255,))
    return im.resize((size, size), Image.LANCZOS)


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def save_ico(path) -> None:
    """Multi-resolution .ico (each size drawn separately, not scaled)."""
    imgs = [icon_image(s) for s in ICO_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                  append_images=imgs[:-1])
