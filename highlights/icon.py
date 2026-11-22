"""Highlights' icon: DRAFT's hextech diamond with a record dot."""
from __future__ import annotations

from PIL import Image, ImageDraw

from review.theme.tokens import ACCENT, DARK_BG


def icon_image(n: int = 256) -> Image.Image:
    s = 4                                  # draw big, scale down: smooth dot
    N = n * s
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (N - 1) / 2
    k = max(2 * s, N // 14)
    d.polygon([(c, 0), (N - 1, c), (c, N - 1), (0, c)], fill=ACCENT)
    d.polygon([(c, k), (N - 1 - k, c), (c, N - 1 - k), (k, c)], fill=DARK_BG)
    r = N * 0.2
    d.ellipse([c - r, c - r, c + r, c + r], fill=ACCENT)
    r2 = N * 0.1
    d.ellipse([c - r2, c - r2, c + r2, c + r2], fill=DARK_BG)
    return im.resize((n, n), Image.LANCZOS)
