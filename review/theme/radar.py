"""The performance radar — REVIEW's signature visual, in DRAFT's language.

Octagon grid in navy tones with alternating ring fills, a dashed ring at 50
(= a typical same-role peer), a translucent gold polygon with a thin gold
outline over a soft CI band, ◆ vertex markers, and original single-colour
line icons per axis (silver, gold on hover/selection). Switching data morphs
the shape. Overlays (previous period, champion vs overall, role vs role,
compare) draw as ghost or teal polygons.
"""
from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable

from . import anim
from .charts import Chart
from .pen import Pen
from .tokens import (ACCENT, BAD, DARK_BG, DIM, EDGE, EDGE_HI, F_SM_B,
                     F_XS, F_XS_B, FG, GHOST, GOOD, GRID, PANEL, PANEL2,
                     RING_A, RING_B, score_color)
from .widgets import px

N_FRAMES = 14


@dataclass
class RadarAxis:
    key: str
    label: str
    score: float
    lo: float = float("nan")
    hi: float = float("nan")
    n: int = 0
    subs: list = field(default_factory=list)   # [(label, pct, you, peer, n)]
    note: str = ""


@dataclass
class Overlay:
    scores: list[float]
    color: str = GHOST
    label: str = ""
    lo: list[float] | None = None
    hi: list[float] | None = None
    dashed: bool = True
    fill_alpha: int = 22


# ---------------------------------------------------------------- icons

def draw_icon(pen: Pen, key: str, x: float, y: float, s: float, col: str):
    """Original line icons in a box of size s centred on (x, y)."""
    w = max(1.3, s / 11)
    h = s / 2

    def L(*pts):
        pen.line([(x + a * h, y + b * h) for a, b in pts], col, w)
    if key == "fighting":            # crossed blades with guards
        L((-0.85, 0.85), (0.75, -0.75))
        L((0.85, 0.85), (-0.75, -0.75))
        L((-0.75, 0.35), (-0.35, 0.75))
        L((0.75, 0.35), (0.35, 0.75))
    elif key == "farming":           # wheat stalk
        L((0, 0.95), (0, -0.85))
        for yy in (-0.45, 0.0, 0.45):
            L((0, yy + 0.2), (-0.5, yy - 0.15))
            L((0, yy + 0.2), (0.5, yy - 0.15))
    elif key == "vision":            # eye
        top = [(math.cos(t) * 0.95, -math.sin(t) * 0.55)
               for t in [i * math.pi / 12 for i in range(13)]]
        L(*top)
        L(*[(a, -b) for a, b in top])
        pen.circle(x, y, h * 0.28, outline=col, width=w)
    elif key == "aggression":        # three claw slashes
        for dx in (-0.45, 0.0, 0.45):
            L((dx - 0.25, 0.85), (dx + 0.25, -0.85))
    elif key == "survival":          # shield
        L((-0.75, -0.75), (0, -0.95), (0.75, -0.75), (0.7, 0.1),
          (0, 0.95), (-0.7, 0.1), (-0.75, -0.75))
        L((0, -0.5), (0, 0.45))
    elif key == "teamplay":          # three linked nodes
        pts = [(0, -0.62), (-0.68, 0.55), (0.68, 0.55)]
        for (a, b), (c, d) in ((pts[0], pts[1]), (pts[1], pts[2]),
                               (pts[2], pts[0])):
            L((a, b), (c, d))
        for a, b in pts:
            pen.circle(x + a * h, y + b * h, h * 0.24, fill=PANEL,
                       outline=col, width=w)
    elif key == "objectives":        # banner on a pole
        L((-0.6, 0.95), (-0.6, -0.95))
        L((-0.6, -0.9), (0.75, -0.6), (-0.6, -0.2))
        L((-0.95, 0.95), (-0.25, 0.95))
    elif key == "consistency":       # bullseye
        pen.circle(x, y, h * 0.9, outline=col, width=w)
        pen.circle(x, y, h * 0.5, outline=col, width=w)
        pen.circle(x, y, h * 0.14, fill=col)


# ---------------------------------------------------------------- widget

class RadarChart(Chart):
    margins = (0, 0, 0, 0)

    def __init__(self, master, tooltip=None, height=420, thumb=False,
                 on_axis: Callable[[str], None] | None = None, bg=PANEL,
                 peer_word: str = "peer"):
        super().__init__(master, tooltip, height, bg)
        self.thumb = thumb
        self.scale = None             # (lo, hi); None = SCALE_LO..SCALE_HI
        self.on_axis = on_axis
        self.peer_word = peer_word
        self.axes: list[RadarAxis] = []
        self.overlays: list[Overlay] = []
        self.shown: list[float] | None = None      # currently drawn scores
        self.hover_i = -1
        self.selected: str | None = None
        self._anim = None
        self.bind("<Button-1>", self._click)

    # data
    def set_radar(self, axes: list[RadarAxis], overlays=(), animate=True):
        old = self.shown
        self.axes, self.overlays = axes, list(overlays)
        target = [a.score for a in axes]
        if animate and old and len(old) == len(target) and \
                self.winfo_ismapped():
            self._morph(old, target)
        else:
            self.shown = target
            self.data = {"_": 1}
            self.schedule(1)

    def grow(self):
        """Animate the shape out of the centre (first reveal)."""
        if not self.axes:
            return
        target = [a.score for a in self.axes]
        self._morph([SCALE_LO] * len(target), target)

    def _morph(self, frm, to, k=0):
        if self._anim:
            self.after_cancel(self._anim)
        t = anim.ease_out(k / N_FRAMES)
        self.shown = [a + (b - a) * t if _ok(a) and _ok(b) else b
                      for a, b in zip(frm, to)]
        self.data = {"_": 1}
        # in-between frames without supersampling (4x fewer pixels); the
        # final frame is drawn at full quality
        self._ss = 2 if k >= N_FRAMES else 1
        self.render()
        self._ss = 2
        if k < N_FRAMES:
            self._anim = self.after(anim.FRAME_MS,
                                    lambda: self._morph(frm, to, k + 1))
        else:
            self._anim = None

    # geometry
    def _geom(self, w, h):
        lab = px(24) if self.thumb else px(78)
        R = max(10, min(w, h) / 2 - lab)
        return w / 2, h / 2 + (0 if self.thumb else px(4)), R

    def _pt(self, cx, cy, R, i, v, n):
        a = -math.pi / 2 + 2 * math.pi * i / n
        r = R * radial(v, *(self.scale or (SCALE_LO, SCALE_HI)))
        return cx + r * math.cos(a), cy + r * math.sin(a)

    def draw(self, pen: Pen, w, h, _=None):
        n = len(self.axes)
        if n < 3:
            return
        cx, cy, R = self._geom(w, h)
        self.cx, self.cy, self.R = cx, cy, R
        ring = lambda v: [self._pt(cx, cy, R, i, v, n) for i in range(n)]
        # concentric octagons (zoomed scale, see RADIAL), alternating
        # fills, the 50 ring highlighted, ring values on the top spoke
        lo, hi = self.scale or (SCALE_LO, SCALE_HI)
        step = 10 if hi - lo <= 60 else 20
        rings = list(range(hi, lo, -step))
        for k, v in enumerate(rings):
            pen.poly(ring(v), fill=RING_B if k % 2 == 0 else RING_A)
        for v in rings:
            pen.poly(ring(v), outline=GRID, width=1)
        if not self.thumb:
            pen.poly(ring(50), outline=EDGE, width=1, outline_alpha=200)
            for v in ((30, 50, 70) if hi - lo <= 60 else (50,)):
                p = self._pt(cx, cy, R, 0, v, n)
                pen.text(p[0] + px(4), p[1] - px(1), str(v), F_XS,
                         EDGE_HI if v == 50 else DIM, "lm", alpha=180)
        for i in range(n):
            sel = self.axes[i].key == self.selected or i == self.hover_i
            pen.line([(cx, cy), self._pt(cx, cy, R, i, 100, n)],
                     EDGE_HI if sel and not self.thumb else GRID,
                     1, alpha=200 if sel else 255)
        # overlays (behind the main shape)
        for ov in self.overlays:
            pts = [self._pt(cx, cy, R, i, v if _ok(v) else 50, n)
                   for i, v in enumerate(ov.scores)]
            if ov.lo and ov.hi and not self.thumb:
                pen.band([self._pt(cx, cy, R, i, v, n)
                          for i, v in enumerate(_fill(ov.hi))],
                         [self._pt(cx, cy, R, i, v, n)
                          for i, v in enumerate(_fill(ov.lo))],
                         ov.color, 40)
            pen.poly(pts, fill=ov.color, alpha=ov.fill_alpha)
            if ov.dashed:
                pen.line(pts + [pts[0]], ov.color, 1.4, dash=(5, 4))
            else:
                pen.poly(pts, outline=ov.color, width=1.8)
        # CI band + main polygon
        shown = self.shown or [a.score for a in self.axes]
        if not self.thumb and all(_ok(a.lo) for a in self.axes) and \
                self.shown == [a.score for a in self.axes]:
            pen.band([self._pt(cx, cy, R, i, a.hi, n)
                      for i, a in enumerate(self.axes)],
                     [self._pt(cx, cy, R, i, a.lo, n)
                      for i, a in enumerate(self.axes)], EDGE_HI, 46)
        main = [self._pt(cx, cy, R, i, v if _ok(v) else 50, n)
                for i, v in enumerate(shown)]
        pen.poly(main, fill=ACCENT, alpha=78)
        pen.poly(main, outline=EDGE_HI, width=1.2 if self.thumb else 2)
        if not self.thumb:
            for i, (x, y) in enumerate(main):
                pen.diamond(x, y, px(4.5 if i == self.hover_i else 3.5),
                            EDGE_HI, DARK_BG)
        else:
            return
        # axis icons, labels, scores
        for i, a in enumerate(self.axes):
            sel = a.key == self.selected or i == self.hover_i
            col = EDGE_HI if sel else DIM
            ang = -math.pi / 2 + 2 * math.pi * i / n
            lx = cx + (R + px(40)) * math.cos(ang)
            ly = cy + (R + px(34)) * math.sin(ang)
            draw_icon(pen, a.key, lx, ly - px(14), px(16), col)
            pen.text(lx, ly + px(5), a.label.upper(), F_XS_B, col, "mm")
            sc = shown[i]
            pen.text(lx, ly + px(19), "%.0f" % sc if _ok(sc) else "—",
                     F_SM_B, score_color(sc) if _ok(sc) else DIM, "mm")

    # interaction
    def _axis_at(self, x, y):
        if self.thumb or not self.axes or not hasattr(self, "cx"):
            return -1
        dx, dy = x - self.cx, y - self.cy
        if math.hypot(dx, dy) < self.R * 0.22:
            return -1
        n = len(self.axes)
        a = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
        return int(round(a / (2 * math.pi / n))) % n

    def _motion(self, e):
        i = self._axis_at(e.x, e.y)
        if i != self.hover_i:
            self.hover_i = i
            self.configure(cursor="hand2" if i >= 0 else "")
            if not self._anim:
                self.render()
        if i < 0 or not self.tooltip:
            if self.tooltip:
                self.tooltip.hide()
            return
        self.tooltip.show(e.x_root, e.y_root, build=self._tip(self.axes[i]))

    def _leave(self):
        if self.hover_i != -1:
            self.hover_i = -1
            self.render()
        if self.tooltip:
            self.tooltip.hide()

    def _click(self, e):
        i = self._axis_at(e.x, e.y)
        if i >= 0 and self.on_axis:
            self.selected = self.axes[i].key
            self.render()
            self.on_axis(self.axes[i].key)

    def _tip(self, a: RadarAxis):
        def build(f):
            ci = " [%.0f–%.0f]" % (a.lo, a.hi) if _ok(a.lo) else ""
            tk.Label(f, text="%s   %.0f%s" % (a.label.upper(), a.score, ci),
                     bg=PANEL, fg=EDGE_HI, font=F_SM_B).pack(anchor="w")
            tk.Label(f, text=a.note or "Beats a typical same-role %s in "
                     "%.0f%% of comparisons · n=%d" % (self.peer_word,
                                                       a.score, a.n),
                     bg=PANEL, fg=DIM, font=F_XS).pack(anchor="w",
                                                       pady=(0, px(4)))
            grid = tk.Frame(f, bg=PANEL)
            grid.pack(anchor="w")
            for r, (label, pct, you, peer, n) in enumerate(a.subs):
                tk.Label(grid, text=label, bg=PANEL, fg=FG, font=F_XS,
                         anchor="w").grid(row=r, column=0, sticky="w")
                c = tk.Canvas(grid, bg=PANEL2, width=px(70), height=px(7),
                              highlightthickness=0)
                c.grid(row=r, column=1, padx=px(6))
                if _ok(pct):
                    x = px(70) * pct / 100
                    m = px(35)
                    c.create_rectangle(min(x, m), 0, max(x, m), px(7),
                                       fill=GOOD if pct >= 50 else BAD,
                                       width=0)
                    c.create_line(m, 0, m, px(7), fill=EDGE_HI)
                tk.Label(grid, text="%s" % ("%.0f" % pct if _ok(pct) else "—"),
                         bg=PANEL, fg=score_color(pct) if _ok(pct) else DIM,
                         font=F_XS_B, width=3).grid(row=r, column=2)
                tk.Label(grid, text="you %s · %ss %s" % (you, self.peer_word,
                                                          peer),
                         bg=PANEL, fg=DIM, font=F_XS).grid(row=r, column=3,
                                                           sticky="w")
            tk.Label(f, text="click for the drill-down", bg=PANEL, fg=DIM,
                     font=F_XS).pack(anchor="w", pady=(px(4), 0))
        return build


def _ok(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def _fill(vs):
    return [v if _ok(v) else 50.0 for v in vs]


# The radar is zoomed to 20–80: an axis score is a mean of per-game
# percentiles, so real players sit roughly between 35 and 65 and a 0–100
# scale draws everyone as a near-circle round the 50 ring. Values outside
# the range are pinned to the centre / rim (the number label stays exact).
SCALE_LO, SCALE_HI = 20, 80


def radial(v, lo: float = SCALE_LO, hi: float = SCALE_HI) -> float:
    """Score -> fraction of the radius on the (zoomed) scale."""
    if not _ok(v):
        v = 50.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def thumbnail(scores: list[float], size: int, bg=PANEL2):
    """Small static radar image (PIL) for table cells / small multiples."""
    from PIL import ImageTk
    n = len(scores)
    pen = Pen(size, size, bg=bg)
    cx = cy = size / 2
    R = size / 2 - 2
    def ring(v):
        return [(cx + R * radial(v) * math.cos(-math.pi / 2 + 2 * math.pi
                                              * i / n),
                 cy + R * radial(v) * math.sin(-math.pi / 2 + 2 * math.pi
                                              * i / n)) for i in range(n)]
    pen.poly(ring(SCALE_HI), fill=RING_A, outline=GRID)
    pen.poly(ring(50), outline=EDGE, width=0.8)
    pts = [(cx + R * radial(v) * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
            cy + R * radial(v) * math.sin(-math.pi / 2 + 2 * math.pi * i / n))
           for i, v in enumerate(scores)]
    pen.poly(pts, fill=ACCENT, alpha=95)
    pen.poly(pts, outline=EDGE_HI, width=1)
    return ImageTk.PhotoImage(pen.finish())
