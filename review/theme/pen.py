"""Off-screen renderer for charts (PIL), supersampled for antialiasing.

Charts draw in canvas pixel coordinates; ``zoom`` scales everything for PNG
export, so the export is the same picture at higher resolution. Shapes are
drawn at ``ss``× and downsampled (smooth edges); text is drawn afterwards at
native resolution (crisp glyphs).
"""
from __future__ import annotations

import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import widgets
from .tokens import FONT_FILES, PANEL, SERIF, UI, hex_rgb

_FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
              "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]


@lru_cache(maxsize=None)
def _font_file(family: str, bold: bool) -> str | None:
    for name in FONT_FILES.get((family, bold), ()):
        for d in _FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


@lru_cache(maxsize=256)
def pil_font(spec: tuple, zoom: float = 1.0):
    family, size = spec[0], spec[1]
    bold = len(spec) > 2 and "bold" in spec[2]
    px = max(6, int(round(size * widgets.SCALE * 96 / 72 * zoom)))
    f = _font_file(family if family in (UI, SERIF) else UI, bold)
    try:
        return ImageFont.truetype(f, px) if f else ImageFont.load_default()
    except OSError:
        return ImageFont.load_default()


def rgba(c: str, alpha: int = 255):
    return hex_rgb(c) + (alpha,)


class Pen:
    def __init__(self, w: int, h: int, bg: str = PANEL, zoom: float = 1.0,
                 ss: int = 2):
        self.w, self.h, self.zoom, self.ss = w, h, zoom, ss
        self.k = zoom * ss
        # RGB base: Pillow only alpha-BLENDS "RGBA" fills onto RGB images
        # (on an RGBA image it would overwrite pixels -> opaque fills)
        self.img = Image.new("RGB", (max(1, int(w * self.k)),
                                     max(1, int(h * self.k))), hex_rgb(bg))
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.texts: list = []
        self.overlays: list = []   # images pasted after downsampling

    # coordinates
    def _p(self, pts):
        k = self.k
        return [(x * k, y * k) for x, y in pts]

    def _w(self, width: float) -> int:
        return max(1, int(round(width * self.k)))

    # shapes
    def line(self, pts, color: str, width: float = 1.0, alpha: int = 255,
             dash: tuple[float, float] | None = None):
        if len(pts) < 2:
            return
        if dash:
            for a, b in _dash_segments(pts, *dash):
                self.d.line(self._p([a, b]), fill=rgba(color, alpha),
                            width=self._w(width))
            return
        self.d.line(self._p(pts), fill=rgba(color, alpha),
                    width=self._w(width), joint="curve")

    def rect(self, x0, y0, x1, y1, fill: str | None = None,
             outline: str | None = None, width: float = 1, alpha: int = 255):
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        self.d.rectangle(self._p([(x0, y0), (x1, y1)]),
                         fill=rgba(fill, alpha) if fill else None,
                         outline=rgba(outline, alpha) if outline else None,
                         width=self._w(width) if outline else 0)

    def poly(self, pts, fill: str | None = None, outline: str | None = None,
             width: float = 1, alpha: int = 255, outline_alpha: int = 255):
        if len(pts) < 3:
            return
        if fill:
            self.d.polygon(self._p(pts), fill=rgba(fill, alpha))
        if outline:
            self.line(list(pts) + [pts[0]], outline, width, outline_alpha)

    def circle(self, x, y, r, fill: str | None = None,
               outline: str | None = None, width: float = 1, alpha=255):
        k = self.k
        self.d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k],
                       fill=rgba(fill, alpha) if fill else None,
                       outline=rgba(outline, alpha) if outline else None,
                       width=self._w(width) if outline else 0)

    def diamond(self, x, y, r, fill: str, outline: str | None = None):
        pts = [(x, y - r), (x + r, y), (x, y + r), (x - r, y)]
        self.poly(pts, fill=fill)
        if outline:
            self.poly(pts, outline=outline, width=1)

    def band(self, outer, inner, color: str, alpha: int):
        """Fill the ring between two polygons (radar CI)."""
        po, pi = self._p(outer), self._p(inner)
        xs = [p[0] for p in po + pi]
        ys = [p[1] for p in po + pi]
        x0, y0 = max(int(min(xs)) - 1, 0), max(int(min(ys)) - 1, 0)
        x1 = min(int(max(xs)) + 2, self.img.size[0])
        y1 = min(int(max(ys)) + 2, self.img.size[1])
        if x1 <= x0 or y1 <= y0:
            return
        # only the polygon's bounding box (a full-canvas mask cost ~40 ms)
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        md = ImageDraw.Draw(mask)
        md.polygon([(x - x0, y - y0) for x, y in po], fill=alpha)
        md.polygon([(x - x0, y - y0) for x, y in pi], fill=0)
        self.img.paste(Image.new("RGB", mask.size, hex_rgb(color)),
                       (x0, y0), mask)

    def heat(self, points, radius: float, color: str, max_alpha=200):
        """Density glow (death heatmaps): points are accumulated into a
        grid, Gaussian-smoothed, and mapped to alpha with a sqrt curve, so
        hot spots stand out without empty areas looking hot."""
        if not points:
            return
        import numpy as np
        W_, H_ = self.img.size
        ds = 4                                    # work at 1/4 resolution
        gw, gh = max(W_ // ds, 1), max(H_ // ds, 1)
        grid = np.zeros((gh, gw), float)
        xs = np.clip((np.array([p[0] for p in points]) * self.k / ds)
                     .astype(int), 0, gw - 1)
        ys = np.clip((np.array([p[1] for p in points]) * self.k / ds)
                     .astype(int), 0, gh - 1)
        np.add.at(grid, (ys, xs), 1.0)
        sig = max(radius * self.k / ds, 1.0)
        r = int(3 * sig)
        t = np.arange(-r, r + 1)
        ker = np.exp(-t ** 2 / (2 * sig ** 2))
        ker /= ker.sum()
        grid = np.apply_along_axis(lambda v: np.convolve(v, ker, "same"),
                                   1, grid)
        grid = np.apply_along_axis(lambda v: np.convolve(v, ker, "same"),
                                   0, grid)
        mx = grid.max() or 1.0
        a = np.sqrt(grid / mx) * max_alpha
        a[a < 8] = 0
        mask = Image.fromarray(a.astype(np.uint8), "L").resize(
            (W_, H_), Image.BILINEAR)
        self.img.paste(Image.new("RGB", self.img.size, hex_rgb(color)),
                       (0, 0), mask)

    def image(self, im: Image.Image, x, y, w, h, alpha: int = 255):
        im = im.convert("RGBA").resize((max(1, int(w * self.k)),
                                        max(1, int(h * self.k))),
                                       Image.LANCZOS)
        a = im.split()[3]
        if alpha < 255:
            a = a.point(lambda v: v * alpha // 255)
        self.img.paste(im.convert("RGB"), (int(x * self.k), int(y * self.k)),
                       a)

    # text
    def text(self, x, y, s: str, font=None, color: str = "#f0e6d2",
             anchor: str = "lm", alpha: int = 255):
        self.texts.append((x, y, s, font or ("Segoe UI", 8), color, anchor,
                           alpha))

    def measure(self, s: str, font) -> float:
        return pil_font(tuple(font), 1.0).getlength(s)

    def finish(self) -> Image.Image:
        size = (max(1, int(self.w * self.zoom)),
                max(1, int(self.h * self.zoom)))
        if self.img.size == (size[0] * self.ss, size[1] * self.ss):
            # exact integer supersample: a box reduce is the correct filter
            # and ~20x faster than LANCZOS (on-screen charts)
            out = self.img.reduce(self.ss)
        else:
            out = self.img.resize(size, Image.LANCZOS)
        d = ImageDraw.Draw(out, "RGBA")
        z = self.zoom
        for x, y, s, font, color, anchor, alpha in self.texts:
            d.text((x * z, y * z), s, font=pil_font(tuple(font), z),
                   fill=rgba(color, alpha), anchor=anchor)
        return out.convert("RGB")


def _dash_segments(pts, on: float, off: float):
    out = []
    carry, drawing = 0.0, True
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L == 0:
            continue
        pos = 0.0
        while pos < L:
            seg = (on if drawing else off) - carry
            end = min(pos + seg, L)
            if drawing:
                a = (x0 + (x1 - x0) * pos / L, y0 + (y1 - y0) * pos / L)
                b = (x0 + (x1 - x0) * end / L, y0 + (y1 - y0) * end / L)
                out.append((a, b))
            if end - pos < seg:
                carry += end - pos
                break
            carry = 0.0
            pos = end
            drawing = not drawing
    return out
