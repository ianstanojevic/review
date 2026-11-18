"""Original line icons for the five positions, drawn with PIL in the same
style as the radar axis icons (no game assets).

Top / Bot: the lane as an L along the map's top-left / bottom-right edges;
Mid: the diagonal; Jungle: three claw marks; Support: a shield with a plus.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from .tokens import hex_rgb

ROLE_KEYS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def role_icon_image(role: str, size: int, color: str,
                    faint: str | None = None) -> Image.Image:
    """RGBA icon; ``faint`` draws the map square behind lane icons."""
    ss = 6
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = hex_rgb(color) + (255,)
    fcol = hex_rgb(faint) + (150,) if faint else None
    m = S * 0.14                      # margin
    a, b = m, S - m                   # square extent
    w = max(2, int(S * 0.11))         # stroke
    t = max(1, int(S * 0.05))         # faint stroke
    if role in ("TOP", "MIDDLE", "BOTTOM") and fcol:
        d.rectangle([a, a, b, b], outline=fcol, width=t)
    i = w / 2                        # keep the L inside the square
    if role == "TOP":
        d.line([(a + i, b - S * 0.18), (a + i, a + i), (b - S * 0.18, a + i)],
               fill=col, width=w, joint="curve")
    elif role == "BOTTOM":
        d.line([(a + S * 0.18, b - i), (b - i, b - i), (b - i, a + S * 0.18)],
               fill=col, width=w, joint="curve")
    elif role == "MIDDLE":
        d.line([(a + S * 0.06, b - S * 0.06), (b - S * 0.06, a + S * 0.06)],
               fill=col, width=w)
    elif role == "JUNGLE":
        for k in range(3):
            x0 = S * (0.28 + 0.18 * k)
            d.line([(x0 + S * 0.10, S * 0.16), (x0 - S * 0.06, S * 0.84)],
                   fill=col, width=w)
    elif role == "UTILITY":
        cx = S / 2
        shield = [(cx, S * 0.10), (S * 0.86, S * 0.24), (S * 0.80, S * 0.62),
                  (cx, S * 0.92), (S * 0.20, S * 0.62), (S * 0.14, S * 0.24)]
        d.line(shield + [shield[0]], fill=col, width=max(2, int(w * 0.8)),
               joint="curve")
        d.line([(cx, S * 0.34), (cx, S * 0.70)], fill=col, width=w)
        d.line([(S * 0.33, S * 0.50), (S * 0.67, S * 0.50)], fill=col,
               width=w)
    return im.resize((size, size), Image.LANCZOS)


_CACHE: dict = {}


def role_photo_alpha(role: str, size: int, color: str,
                     faint: str | None = None):
    """Transparent Tk PhotoImage (fits any row/button colour; cached)."""
    key = ("a", role, size, color, faint)
    if key not in _CACHE:
        from PIL import ImageTk
        _CACHE[key] = ImageTk.PhotoImage(role_icon_image(role, size, color,
                                                         faint))
    return _CACHE[key]


def role_photo(role: str, size: int, color: str, bg: str,
               faint: str | None = None):
    """Tk PhotoImage on a solid background (cached)."""
    key = (role, size, color, bg, faint)
    if key not in _CACHE:
        from PIL import ImageTk
        base = Image.new("RGBA", (size, size), hex_rgb(bg) + (255,))
        base.alpha_composite(role_icon_image(role, size, color, faint))
        _CACHE[key] = ImageTk.PhotoImage(base.convert("RGB"))
    return _CACHE[key]
