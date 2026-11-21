"""Replay's icon: DRAFT's hextech diamond with a play triangle."""
from __future__ import annotations

from PIL import Image, ImageDraw

from review.theme.tokens import ACCENT, DARK_BG


def icon_image(n: int = 256) -> Image.Image:
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (n - 1) / 2
    k = max(2, n // 14)
    d.polygon([(c, 0), (n - 1, c), (c, n - 1), (0, c)], fill=ACCENT)
    d.polygon([(c, k), (n - 1 - k, c), (c, n - 1 - k), (k, c)], fill=DARK_BG)
    t = n * 0.22
    d.polygon([(c - t * 0.7, c - t), (c + t, c), (c - t * 0.7, c + t)],
              fill=ACCENT)
    return im
