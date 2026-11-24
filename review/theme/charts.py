"""Reusable chart components in DRAFT's theme.

Every chart: dark panel background, thin gridlines, no chart junk, teal for
the player, muted grey/gold for the baseline, hover tooltips/crosshairs, n
and uncertainty shown, and PNG export (same renderer, 2×) including its
title and one-line takeaway.

    ch = LineChart(parent, tooltip, height=220)
    ch.set_meta("ROLLING CS/MIN", "n=147", "Stable: CS/min −0.2 over 147 games")
    ch.set(series=[...])
"""
from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog
from typing import Any, Callable

from PIL import Image, ImageTk

from . import widgets
from .pen import Pen
from .tokens import (BAD, DARK_BG, DIM, EDGE, EDGE_HI, F_SM, F_SM_B, F_XS,
                     F_XS_B, FG, GHOST, GOOD, GRID, GRID_SOFT, HOVER, PANEL,
                     PANEL2, hex_rgb, mix)
from .widgets import px


# ------------------------------------------------------------------ helpers

def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return []
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw),
               default=10 * mag)
    start = math.ceil(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _finite(xs):
    return [x for x in xs if x is not None and isinstance(x, (int, float))
            and math.isfinite(x)]


def fmt_pct(v: float) -> str:
    return "%.0f%%" % (100 * v)


@dataclass
class Axes:
    x0: float
    y0: float
    x1: float
    y1: float
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    def X(self, v):
        return self.x0 + (v - self.xmin) / ((self.xmax - self.xmin) or 1) * (
            self.x1 - self.x0)

    def Y(self, v):
        return self.y1 - (v - self.ymin) / ((self.ymax - self.ymin) or 1) * (
            self.y1 - self.y0)

    def inv_x(self, px_):
        return self.xmin + (px_ - self.x0) / ((self.x1 - self.x0) or 1) * (
            self.xmax - self.xmin)


def y_grid(pen: Pen, ax: Axes, ticks, fmt: Callable[[float], str],
           zero: bool = False):
    for t in ticks:
        y = ax.Y(t)
        if ax.y0 - 1 <= y <= ax.y1 + 1:
            pen.line([(ax.x0, y), (ax.x1, y)],
                     EDGE if (zero and abs(t) < 1e-12) else GRID, 1)
            pen.text(ax.x0 - px(6), y, fmt(t), F_XS, DIM, "rm")


def _left_overflow(pen) -> float:
    """How far any text sticks out past x=0 (0 when none does)."""
    worst = 0.0
    for x, _y, s, font, _c, anchor, _a in pen.texts:
        if x > px(160) or not s:
            continue
        w = pen.measure(s, font)
        left = x - w if anchor[0] == "r" else x - w / 2             if anchor[0] == "m" else x
        worst = max(worst, -left)
    return worst


# --------------------------------------------------------------- base class

class Chart(tk.Canvas):
    margins = (44, 12, 12, 24)        # left, top, right, bottom (logical px)
    ytitle_extra = 0                  # extra room above the plot for ylabel

    def __init__(self, master, tooltip: widgets.Tooltip | None = None,
                 height: int = 220, bg: str = PANEL):
        super().__init__(master, bg=bg, highlightthickness=0, width=1,
                         height=px(height))
        self.tooltip, self.bgc = tooltip, bg
        self.data: dict = {}
        self.title = self.subtitle = self.takeaway = ""
        self._photo = None
        self._job = None
        self.hits: list[tuple[float, float, float, float, Any]] = []
        self._ver = 0                 # bumped by set(): data changed
        self._drawn = None            # (w, h, ver) of the last render
        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._leave())
        self.bind("<Button-3>", lambda e: self.export_dialog())

    # data
    def set(self, **data):
        self.data = data
        self._ver += 1
        self.schedule()

    def _on_configure(self, e):
        # re-showing a tab fires <Configure> at the same size: nothing to do
        if self._drawn == (e.width, e.height, self._ver):
            return
        self.schedule()

    def set_meta(self, title="", subtitle="", takeaway=""):
        self.title, self.subtitle, self.takeaway = title, subtitle, takeaway

    def schedule(self, delay=25):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(delay, self.render)

    def plot_box(self, w, h):
        l, t, r, b = (px(v) for v in self.margins)
        et, eb = getattr(self, "_axis_pad", (0, 0))
        return l + getattr(self, "_lfix", 0), t + et, w - r, h - b - eb

    def _draw_all(self, pen, w, h):
        """draw() plus the axis titles: ``ylabel`` top-left above the plot
        ("↑ win rate"), ``xlabel`` bottom-right under it ("minute →").
        The plot box shrinks to make room, so nothing overlaps."""
        data = dict(self.data)
        xl, yl = data.pop("xlabel", ""), data.pop("ylabel", "")
        self._axis_pad = (px(12 + self.ytitle_extra) if yl else 0,
                          px(13) if xl else 0)
        try:
            self.draw(pen, w, h, **data)
        finally:
            x0, y0, x1, y1 = self.plot_box(w, h)
            self._axis_pad = (0, 0)
        if yl:
            pen.text(px(4), y0 - px(9 + self.ytitle_extra), "↑ " + yl,
                     F_XS_B, DIM, "lm")
        if xl:
            pen.text(w - px(6), h - px(7), xl + " →", F_XS_B, DIM, "rm")

    # rendering
    def render(self):
        self._job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        pen = Pen(w, h, bg=self.bgc, ss=getattr(self, "_ss", 2))
        self.hits = []
        if self.data:
            self._draw_all(pen, w, h)
            # long y labels ("Emerald III") ran off the left edge: widen
            # the left margin by the overflow and draw once more
            over = _left_overflow(pen)
            if over > 0 and getattr(self, "_lfix", 0) < px(120):
                self._lfix = getattr(self, "_lfix", 0) + over + px(4)
                pen = Pen(w, h, bg=self.bgc, ss=getattr(self, "_ss", 2))
                self.hits = []
                self._draw_all(pen, w, h)
        else:
            pen.text(w / 2, h / 2, "no data yet", F_XS, DIM, "mm")
        self._photo = ImageTk.PhotoImage(pen.finish())
        self._drawn = (w, h, self._ver)
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw", tags="base")
        exp = self.create_text(w - px(4), px(2), text="⤓", anchor="ne",
                               fill=PANEL2, font=F_SM, tags="export")
        self.tag_bind(exp, "<Enter>", lambda e: (
            self.itemconfigure(exp, fill=EDGE_HI), self.configure(cursor="hand2"),
            self.tooltip and self.tooltip.show(e.x_root, e.y_root,
                                               "Export this chart as PNG")))
        self.tag_bind(exp, "<Leave>", lambda e: (
            self.itemconfigure(exp, fill=PANEL2), self.configure(cursor="")))
        self.tag_bind(exp, "<Button-1>", lambda e: self.export_dialog())

    def draw(self, pen: Pen, w: int, h: int, **data):   # pragma: no cover
        raise NotImplementedError

    # hover: default = rectangular hit regions -> tooltip text/builder
    def _motion(self, e):
        self.delete("ov")
        hit = self.hover(e.x, e.y)
        if hit is None:
            if self.tooltip:
                self.tooltip.hide()
            return
        if self.tooltip:
            if callable(hit):
                self.tooltip.show(e.x_root, e.y_root, build=hit)
            else:
                self.tooltip.show(e.x_root, e.y_root, str(hit))

    def hover(self, x, y):
        for x0, y0, x1, y1, tip in self.hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.create_rectangle(x0, y0, x1, y1, outline=EDGE_HI,
                                      tags="ov")
                return tip
        return None

    def _leave(self):
        self.delete("ov")
        if self.tooltip:
            self.tooltip.hide()

    # export
    def export_image(self, zoom: float = 2.0) -> Image.Image:
        w, h = self.winfo_width(), self.winfo_height()
        head = px(26) if self.title else 0
        foot = px(22) if self.takeaway else 0
        pen = Pen(w, h + head + foot + px(8), bg=PANEL, zoom=zoom)
        if self.title:
            pen.text(px(10), px(14), self.title, F_SM_B, EDGE_HI, "lm")
            pen.line([(px(10), px(24)), (w - px(10), px(24))], EDGE_HI, 2)
            if self.subtitle:
                pen.text(w - px(10), px(14), self.subtitle, F_XS, DIM, "rm")
        chart = Pen(w, h, bg=self.bgc, zoom=zoom)
        self.hits = []
        if self.data:
            self._draw_all(chart, w, h)
        img = pen.finish()
        img.paste(chart.finish(), (0, int(head * zoom)))
        if self.takeaway:
            from PIL import ImageDraw
            from .pen import pil_font, rgba
            ImageDraw.Draw(img).text(
                (px(10) * zoom, (head + h + px(12)) * zoom), self.takeaway,
                font=pil_font(F_SM, zoom), fill=rgba(FG), anchor="lm")
        self.render()          # restore on-screen hit regions
        return img

    def export_dialog(self):
        name = (self.title or "chart").lower().replace(" ", "_")
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".png", initialfile=name + ".png",
            filetypes=[("PNG image", "*.png")], title="Export chart")
        if path:
            self.export_image().save(path)


# ------------------------------------------------------------- line chart

@dataclass
class Series:
    xs: list[float]
    ys: list[float]
    color: str = GOOD
    name: str = ""
    lo: list[float] | None = None
    hi: list[float] | None = None
    ns: list[int] | None = None
    width: float = 2.0
    dash: tuple[float, float] | None = None
    band_alpha: int = 46
    fmt: Callable[[float], str] = field(default=lambda v: "%.2f" % v)


class LineChart(Chart):
    """Lines with optional CI bands, shaded regions (e.g. patches) and a
    crosshair that lists every series' value ± and n at the hovered x."""

    def draw(self, pen, w, h, series: list[Series], xfmt=lambda v: "%g" % v,
             yfmt=lambda v: "%g" % v, zero=False, regions=(), ylim=None,
             xticks=None, baseline: float | None = None,
             baseline_label: str = ""):
        x0, y0, x1, y1 = self.plot_box(w, h)
        xs = _finite([x for s in series for x in s.xs])
        ys = _finite([v for s in series for v in
                      (s.ys + (s.lo or []) + (s.hi or []))])
        if baseline is not None:
            ys.append(baseline)
        if zero:
            ys.append(0.0)
        if not xs or not ys:
            pen.text(w / 2, h / 2, "not enough data", F_XS, DIM, "mm")
            return
        lo, hi = ylim or (min(ys), max(ys))
        pad = (hi - lo) * 0.08 or 1
        if not ylim:
            lo, hi = lo - pad, hi + pad
        ax = Axes(x0, y0, x1, y1, min(xs), max(xs), lo, hi)
        self.ax = ax
        for i, (a, b, label) in enumerate(regions):
            if i % 2:
                pen.rect(ax.X(a), y0, ax.X(b), y1, fill=GRID_SOFT)
            if ax.X(b) - ax.X(a) < px(30):    # too narrow to label
                continue
            pen.text((ax.X(a) + ax.X(b)) / 2, y0 + px(7), label, F_XS, DIM,
                     "mm")
        y_grid(pen, ax, nice_ticks(lo, hi, 4), yfmt, zero)
        for t in (xticks or nice_ticks(ax.xmin, ax.xmax, 6)):
            if ax.xmin <= t <= ax.xmax:
                pen.text(ax.X(t), y1 + px(11), xfmt(t), F_XS, DIM, "mm")
        if baseline is not None:
            yb = ax.Y(baseline)
            pen.line([(x0, yb), (x1, yb)], EDGE_HI, 1, alpha=170, dash=(5, 4))
            if baseline_label:
                pen.text(x1, yb - px(7), baseline_label, F_XS, EDGE_HI, "rm")
        for s in series:
            if s.lo and s.hi:
                top = [(ax.X(x), ax.Y(v)) for x, v in zip(s.xs, s.hi)
                       if _ok(v)]
                bot = [(ax.X(x), ax.Y(v)) for x, v in zip(s.xs, s.lo)
                       if _ok(v)]
                if len(top) > 1:
                    pen.poly(top + bot[::-1], fill=s.color, alpha=s.band_alpha)
        for s in series:
            pts = [(ax.X(x), ax.Y(v)) for x, v in zip(s.xs, s.ys) if _ok(v)]
            pen.line(pts, s.color, s.width, dash=s.dash)
        self.series = series

    def hover(self, x, y):
        ax = getattr(self, "ax", None)
        if not ax or not (ax.x0 <= x <= ax.x1):
            return None
        xv = ax.inv_x(x)
        lines = []
        xs_all = sorted({xx for s in self.series for xx in s.xs})
        if not xs_all:
            return None
        xn = min(xs_all, key=lambda v: abs(v - xv))
        X = ax.X(xn)
        self.create_line(X, ax.y0, X, ax.y1, fill=EDGE_HI, dash=(2, 3),
                         tags="ov")
        for s in self.series:
            if xn in s.xs:
                i = s.xs.index(xn)
                v = s.ys[i]
                if not _ok(v):
                    continue
                Y = ax.Y(v)
                r = px(3)
                self.create_oval(X - r, Y - r, X + r, Y + r, fill=s.color,
                                 outline=DARK_BG, tags="ov")
                ci = ""
                if s.lo and s.hi and _ok(s.lo[i]):
                    ci = "  [%s – %s]" % (s.fmt(s.lo[i]), s.fmt(s.hi[i]))
                n = "  n=%d" % s.ns[i] if s.ns else ""
                lines.append((s.color, "%s %s%s%s" % (
                    s.name, s.fmt(v), ci, n)))
        xf = self.data.get("xfmt", lambda v: "%g" % v)
        head = self.data.get("hover_x", lambda v: xf(v))(xn)

        def build(f):
            tk.Label(f, text=head, bg=PANEL, fg=EDGE_HI, font=F_XS_B).pack(
                anchor="w")
            for col, t in lines:
                tk.Label(f, text="● " + t, bg=PANEL, fg=col, font=F_XS).pack(
                    anchor="w")
        return build if lines else None


def _ok(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


# ---------------------------------------------------------- binned bars

@dataclass
class BinBar:
    label: str
    value: float
    lo: float = float("nan")
    hi: float = float("nan")
    n: int = 0
    tip: str = ""


class BarChart(Chart):
    """Binned win rates (or any value) with CI whiskers, n under each bar
    and a dashed baseline. Bars above the baseline are teal, below red."""
    margins = (40, 14, 10, 34)

    def draw(self, pen, w, h, bins: list[BinBar], baseline: float = 0.5,
             baseline_label: str = "", ylim=(0.0, 1.0), yfmt=fmt_pct,
             vfmt=fmt_pct, neutral: bool = False):
        x0, y0, x1, y1 = self.plot_box(w, h)
        if not bins:
            pen.text(w / 2, h / 2, "not enough data", F_XS, DIM, "mm")
            return
        lo, hi = ylim
        ax = Axes(x0, y0, x1, y1, 0, len(bins), lo, hi)
        y_grid(pen, ax, nice_ticks(lo, hi, 4), yfmt)
        slot = (x1 - x0) / len(bins)
        bw = min(slot * 0.62, px(46))
        yb = ax.Y(baseline)
        boxes = []                     # value labels drawn (x0, y0, x1, y1)
        for i, b in enumerate(bins):
            cx = x0 + slot * (i + 0.5)
            pen.text(cx, y1 + px(10), b.label, F_XS, FG if b.n else DIM, "mm")
            pen.text(cx, y1 + px(23), "n=%d" % b.n, F_XS, DIM, "mm")
            if not b.n or not _ok(b.value):
                continue
            col = DIM if neutral else (GOOD if b.value >= baseline else BAD)
            alpha = 255 if b.n >= 10 else 150
            yv = ax.Y(max(min(b.value, hi), lo))
            pen.rect(cx - bw / 2, yb, cx + bw / 2, yv, fill=col, alpha=alpha)
            if _ok(b.lo) and _ok(b.hi):
                ylo, yhi = ax.Y(max(b.lo, lo)), ax.Y(min(b.hi, hi))
                pen.line([(cx, ylo), (cx, yhi)], FG, 1, alpha=200)
                for yy in (ylo, yhi):
                    pen.line([(cx - px(4), yy), (cx + px(4), yy)], FG, 1,
                             alpha=200)
            # value label centred over (under) the bar and its whisker:
            # beside the bar it ran into the next bar when slots were narrow
            txt = vfmt(b.value)
            tw = pen.measure(txt, F_XS_B)
            if b.value >= baseline:
                top = min(yv, yb, ax.Y(min(b.hi, hi)) if _ok(b.hi) else yv)
                ty = max(top - px(7), y0 + px(5))
            else:
                bot = max(yv, yb, ax.Y(max(b.lo, lo)) if _ok(b.lo) else yv)
                ty = min(bot + px(7), y1 - px(5))
            pen.text(cx, ty, txt, F_XS_B, col, "mm")
            boxes.append((cx - tw / 2, ty - px(6), cx + tw / 2, ty + px(6)))
            tip = b.tip or "%s: %s  [%s – %s]  n=%d" % (
                b.label, vfmt(b.value), vfmt(b.lo), vfmt(b.hi), b.n)
            self.hits.append((cx - slot / 2, y0, cx + slot / 2, y1, tip))
        pen.line([(x0, yb), (x1, yb)], EDGE_HI, 1, alpha=200, dash=(5, 4))
        if baseline_label:
            # right end of the line, else the left end, whichever no value
            # label covers; a bare "50%" the y axis already shows is dropped
            lw = pen.measure(baseline_label, F_XS)
            ly = yb - px(7)
            spots = [(x1 - lw, x1, "rm", x1), (x0 + px(3), x0 + px(3) + lw,
                                               "lm", x0 + px(3))]
            free = [s for s in spots if not any(
                bx0 < s[1] and bx1 > s[0] and by0 < ly + px(6) and
                by1 > ly - px(6) for bx0, by0, bx1, by1 in boxes)]
            if free:
                _, _, anc, x = free[0]
                pen.text(x, ly, baseline_label, F_XS, EDGE_HI, anc)
            elif baseline_label != yfmt(baseline):
                pen.text(x1, ly, baseline_label, F_XS, EDGE_HI, "rm")


# ------------------------------------------------------- diverging bars

@dataclass
class DivItem:
    label: str
    d: float
    lo: float
    hi: float
    tag: str = ""
    tip: str = ""
    good_if_positive: bool | None = None


class DivergingBars(Chart):
    """Effect sizes around zero: teal = higher in wins, red = higher in
    losses, CI whiskers, a tag column (e.g. 'early')."""
    margins = (12, 10, 12, 20)

    def draw(self, pen, w, h, items: list[DivItem], label_w: int = 250,
             lim: float | None = None, left_label="higher in losses",
             right_label="higher in wins"):
        if not items:
            pen.text(w / 2, h / 2, "not enough data", F_XS, DIM, "mm")
            return
        x0, y0, x1, y1 = self.plot_box(w, h)
        lx = x0 + px(label_w)
        m = lim or max(1.0, max(max(abs(i.lo), abs(i.hi)) for i in items
                                if _ok(i.lo)) * 1.05)
        ax = Axes(lx, y0, x1 - px(40), y1, -m, m, 0, 1)
        row = (y1 - y0) / len(items)
        for t in nice_ticks(-m, m, 6):
            X = ax.X(t)
            pen.line([(X, y0), (X, y1)], EDGE if t == 0 else GRID, 1)
            pen.text(X, y1 + px(9), "%+.1f" % t if t else "0", F_XS, DIM, "mm")
        pen.text(ax.X(-m / 2), y0 - px(2), left_label, F_XS, BAD, "mm")
        pen.text(ax.X(m / 2), y0 - px(2), right_label, F_XS, GOOD, "mm")
        bh = min(row * 0.56, px(14))
        for i, it in enumerate(items):
            cy = y0 + row * (i + 0.5) + px(4)
            pen.text(x0, cy, it.label, F_XS, FG, "lm")
            if it.tag:
                pen.text(lx - px(8), cy, it.tag, F_XS, EDGE_HI, "rm")
            col = GOOD if it.d >= 0 else BAD
            pen.rect(ax.X(0), cy - bh / 2, ax.X(max(-m, min(m, it.d))),
                     cy + bh / 2, fill=col)
            if _ok(it.lo):
                a, b = ax.X(max(-m, it.lo)), ax.X(min(m, it.hi))
                pen.line([(a, cy), (b, cy)], FG, 1, alpha=210)
                for xx in (a, b):
                    pen.line([(xx, cy - px(4)), (xx, cy + px(4))], FG, 1,
                             alpha=210)
            pen.text(x1, cy, "%+.2f" % it.d, F_XS_B, col, "rm")
            self.hits.append((x0, cy - row / 2, x1, cy + row / 2,
                              it.tip or "%s: d = %+.2f [%+.2f, %+.2f]" % (
                                  it.label, it.d, it.lo, it.hi)))


# ----------------------------------------------------------- distribution

class Distribution(Chart):
    """Your per-game values vs the peer distribution (histograms on shared
    bins, normalised to share of games) with both medians marked."""
    margins = (40, 18, 12, 24)
    ytitle_extra = 14                 # axis title above the median legend

    def draw(self, pen, w, h, mine: list[float], peers: list[float],
             fmt=lambda v: "%.1f" % v, bins: int = 14, label_me="You",
             label_peers="Peers"):
        mine, peers = _finite(mine), _finite(peers)
        if len(mine) < 3 or len(peers) < 3:
            pen.text(w / 2, h / 2, "not enough data", F_XS, DIM, "mm")
            return
        allv = sorted(mine + peers)
        lo = allv[int(0.02 * len(allv))]
        hi = allv[int(0.98 * len(allv)) - 1]
        if hi <= lo:
            hi = lo + 1
        step = (hi - lo) / bins

        def hist(vals):
            c = [0] * bins
            for v in vals:
                i = int((min(max(v, lo), hi - 1e-9) - lo) / step)
                c[i] += 1
            return [x / len(vals) for x in c]
        hm, hp = hist(mine), hist(peers)
        x0, y0, x1, y1 = self.plot_box(w, h)
        top = max(hm + hp) * 1.15
        ax = Axes(x0, y0, x1, y1, lo, hi, 0, top)
        y_grid(pen, ax, nice_ticks(0, top, 3), fmt_pct)
        for i in range(bins):
            a, b = ax.X(lo + i * step), ax.X(lo + (i + 1) * step)
            pen.rect(a + 1, ax.Y(hp[i]), b - 1, y1, fill=GHOST, alpha=110)
            pen.rect(a + px(3), ax.Y(hm[i]), b - px(3), y1, fill=GOOD,
                     alpha=200)
            self.hits.append((a, y0, b, y1, "%s – %s: you %.0f%% of games, "
                              "peers %.0f%%" % (fmt(lo + i * step),
                                                fmt(lo + (i + 1) * step),
                                                100 * hm[i], 100 * hp[i])))
        for t in nice_ticks(lo, hi, 5):
            pen.text(ax.X(t), y1 + px(11), fmt(t), F_XS, DIM, "mm")
        med = lambda v: sorted(v)[len(v) // 2]
        for v, col, lab, n in ((med(peers), GHOST, label_peers, len(peers)),
                               (med(mine), GOOD, label_me, len(mine))):
            X = ax.X(min(max(v, lo), hi))
            pen.line([(X, y0), (X, y1)], col, 1.5, dash=(4, 3))
        # legend, right-aligned above the plot, squares in the bar colours
        parts = [("middle game: ", None), ("■ ", GOOD),
                 ("%s %s   " % (label_me, fmt(med(mine))), None),
                 ("■ ", GHOST), ("%s %s" % (label_peers, fmt(med(peers))),
                                 None)]
        x = x1 - sum(pen.measure(t, F_XS) for t, _ in parts)
        for t, col in parts:
            pen.text(x, y0 - px(8), t, F_XS, col or DIM, "lm")
            x += pen.measure(t, F_XS)


# ---------------------------------------------------------------- scatter

@dataclass
class Point:
    x: float
    y: float
    r: float = 5
    lo: float = float("nan")
    hi: float = float("nan")
    label: str = ""
    color: str = GOOD
    tip: str = ""


class Scatter(Chart):
    margins = (44, 14, 14, 26)

    def draw(self, pen, w, h, points: list[Point], xfmt=lambda v: "%g" % v,
             yfmt=fmt_pct, xlim=None, ylim=None, hline: float | None = None,
             vline: float | None = None, quadrants=None, label_top: int = 8,
             xlabel: str = "", curve: list[tuple[float, float]] | None = None,
             vline_label: str = ""):
        if not points:
            pen.text(w / 2, h / 2, "not enough data", F_XS, DIM, "mm")
            return
        x0, y0, x1, y1 = self.plot_box(w, h)
        xs = [p.x for p in points]
        ys = [v for p in points for v in (p.y, p.lo, p.hi) if _ok(v)]
        xl = xlim or (min(xs), max(xs))
        yl = ylim or (min(ys), max(ys))
        xpad = (xl[1] - xl[0]) * 0.06 or 1
        ax = Axes(x0, y0, x1, y1, xl[0] - (0 if xlim else xpad),
                  xl[1] + (0 if xlim else xpad), *yl)
        y_grid(pen, ax, nice_ticks(*yl, 4), yfmt)
        for t in nice_ticks(ax.xmin, ax.xmax, 6):
            pen.text(ax.X(t), y1 + px(11), xfmt(t), F_XS, DIM, "mm")
        # (axis titles are drawn by Chart._draw_all)
        if hline is not None:
            pen.line([(x0, ax.Y(hline)), (x1, ax.Y(hline))], EDGE_HI, 1,
                     alpha=170, dash=(5, 4))
        if vline is not None:
            pen.line([(ax.X(vline), y0), (ax.X(vline), y1)], EDGE_HI, 1,
                     alpha=170 if vline_label else 120, dash=(5, 4))
            if vline_label:
                pen.text(ax.X(vline) + px(4), (y0 + y1) / 2, vline_label,
                         F_XS, EDGE_HI, "lm")
        if quadrants:      # (top-left, top-right, bottom-left, bottom-right)
            tl, tr, bl, br = quadrants
            pen.text(x0 + px(6), y0 + px(6), tl, F_XS_B, DIM, "la")
            pen.text(x1 - px(6), y0 + px(6), tr, F_XS_B, DIM, "ra")
            pen.text(x0 + px(6), y1 - px(6), bl, F_XS_B, DIM, "ls")
            pen.text(x1 - px(6), y1 - px(6), br, F_XS_B, DIM, "rs")
        if curve:
            pen.line([(ax.X(a), ax.Y(b)) for a, b in curve if _ok(b)],
                     EDGE_HI, 2)
        for p in sorted(points, key=lambda p: -p.r):
            X, Y = ax.X(p.x), ax.Y(p.y)
            if _ok(p.lo):
                pen.line([(X, ax.Y(p.lo)), (X, ax.Y(p.hi))], p.color, 1,
                         alpha=120)
            pen.circle(X, Y, px(p.r), fill=p.color, alpha=150,
                       outline=p.color)
            self.hits.append((X - px(p.r) - 2, Y - px(p.r) - 2,
                              X + px(p.r) + 2, Y + px(p.r) + 2,
                              p.tip or p.label))
        # labels: right of the bubble, else left, above or below — never on
        # top of another label or a quadrant title ("Aphelios" and "Xayah"
        # at the same spot printed over each other); no free spot = hover
        taken = []
        if quadrants:
            for txt, x, y, a in ((quadrants[0], x0 + px(6), y0, "l"),
                                 (quadrants[1], x1 - px(6), y0, "r"),
                                 (quadrants[2], x0 + px(6), y1 - px(14), "l"),
                                 (quadrants[3], x1 - px(6), y1 - px(14), "r")):
                tw = pen.measure(txt, F_XS_B)
                bx = x if a == "l" else x - tw
                taken.append((bx, y, bx + tw, y + px(16)))
        for p in sorted(points, key=lambda p: -p.r)[:label_top]:
            if not p.label:
                continue
            X, Y, r = ax.X(p.x), ax.Y(p.y), px(p.r)
            tw, th = pen.measure(p.label, F_XS), px(12)
            spots = [(X + r + px(4), Y, "lm"), (X - r - px(4), Y, "rm"),
                     (X, Y - r - px(8), "mm"), (X, Y + r + px(8), "mm")]
            for sx, sy, anc in spots:
                bx0 = sx if anc == "lm" else sx - tw if anc == "rm"                     else sx - tw / 2
                box = (bx0, sy - th / 2, bx0 + tw, sy + th / 2)
                if box[0] < x0 or box[2] > x1 or box[1] < y0 or box[3] > y1:
                    continue
                if any(box[0] < t[2] and box[2] > t[0] and box[1] < t[3]
                       and box[3] > t[1] for t in taken):
                    continue
                taken.append(box)
                pen.text(sx, sy, p.label, F_XS, FG, anc)
                break


# ---------------------------------------------------------------- heatmap

class Heatmap(Chart):
    """Grid of rates (e.g. weekday × hour): colour = WR, opacity = n."""
    margins = (34, 16, 8, 18)
    ytitle_extra = 14                 # above the hour labels

    def draw(self, pen, w, h, grid, row_labels, col_labels, min_n: int = 1):
        x0, y0, x1, y1 = self.plot_box(w, h)
        R, C = len(row_labels), len(col_labels)
        cw, ch = (x1 - x0) / C, (y1 - y0) / R
        nmax = max((cell.n for row in grid for cell in row), default=1) or 1
        for r in range(R):
            pen.text(x0 - px(6), y0 + ch * (r + 0.5), row_labels[r], F_XS,
                     DIM, "rm")
            for c in range(C):
                cell = grid[r][c]
                a, b = x0 + c * cw + 1, y0 + r * ch + 1
                if cell.n < min_n:
                    pen.rect(a, b, a + cw - 2, b + ch - 2, fill=PANEL2)
                    continue
                p = cell.wins / cell.n
                col = mix(BAD, GOOD, p) if p != 0.5 else FG
                pen.rect(a, b, a + cw - 2, b + ch - 2, fill=PANEL2)
                pen.rect(a, b, a + cw - 2, b + ch - 2, fill=col,
                         alpha=int(70 + 185 * cell.n / nmax))
                self.hits.append((a, b, a + cw, b + ch, "%s %s — %dW %dL "
                                  "(%.0f%%), n=%d" % (
                                      row_labels[r], col_labels[c], cell.wins,
                                      cell.n - cell.wins, 100 * p, cell.n)))
        for c in range(0, C, max(1, C // 8)):
            pen.text(x0 + cw * (c + 0.5), y0 - px(8), col_labels[c], F_XS,
                     DIM, "mm")


# ------------------------------------------------------------- minimap

MAP_MAX = 14870.0


class MapChart(Chart):
    """Summoner's Rift minimap with a death heat glow + dots."""
    margins = (0, 0, 0, 0)

    def __init__(self, master, tooltip=None, height=360, minimap=None,
                 bg=PANEL):
        super().__init__(master, tooltip, height, bg)
        self.minimap = minimap        # PIL image or None

    def draw(self, pen, w, h, points: list[tuple[float, float, str]],
             heat: bool = True, markers: list[tuple] = ()):
        side = min(w, h) - px(4)
        ox, oy = (w - side) / 2, (h - side) / 2
        if self.minimap is not None:
            pen.image(self.minimap, ox, oy, side, side, alpha=150)
        else:
            pen.rect(ox, oy, ox + side, oy + side, fill=PANEL2)
            pen.line([(ox, oy + side), (ox + side, oy)], GRID, 2)
        pen.rect(ox, oy, ox + side, oy + side, fill=DARK_BG, alpha=70)
        pen.rect(ox, oy, ox + side, oy + side, outline=EDGE, width=1)

        def P(x, y):
            return ox + x / MAP_MAX * side, oy + side - y / MAP_MAX * side
        pts = [P(x, y) for x, y, _ in points]
        if heat:
            pen.heat(pts, px(9), BAD, max_alpha=200)
        dot = px(2.2) if len(pts) < 150 else px(1.5)
        dot_a = 200 if len(pts) < 150 else 130
        for (x, y, tip), (X, Y) in zip(points, pts):
            pen.circle(X, Y, dot, fill=FG, alpha=dot_a)
            self.hits.append((X - 4, Y - 4, X + 4, Y + 4, tip))
        for x, y, label, color in markers:
            X, Y = P(x, y)
            pen.diamond(X, Y, px(5), color, DARK_BG)
            pen.text(X + px(7), Y, label, F_XS_B, color, "lm")
            self.hits.append((X - 6, Y - 6, X + 6, Y + 6, label))
        pen.text(ox + px(6), oy + side - px(8), "n=%d deaths" % len(points),
                 F_XS, FG, "lm")


# --------------------------------------------------------------- sparkline

class Sparkline(Chart):
    margins = (2, 4, 2, 4)

    def __init__(self, master, tooltip=None, width=110, height=28, bg=PANEL):
        super().__init__(master, tooltip, height, bg)
        self.configure(width=px(width))
        self.unbind("<Button-3>")

    def render(self):
        super().render()
        self.delete("export")

    def draw(self, pen, w, h, ys: list[float], color=EDGE_HI, mid=50.0,
             tip: str = ""):
        ys = [y for y in ys if _ok(y)]
        if len(ys) < 2:
            return
        x0, y0, x1, y1 = self.plot_box(w, h)
        lo, hi = min(ys + [mid]), max(ys + [mid])
        ax = Axes(x0, y0, x1, y1, 0, len(ys) - 1, lo - 1, hi + 1)
        pen.line([(x0, ax.Y(mid)), (x1, ax.Y(mid))], GRID, 1)
        pen.line([(ax.X(i), ax.Y(v)) for i, v in enumerate(ys)], color, 1.6)
        pen.circle(ax.X(len(ys) - 1), ax.Y(ys[-1]), px(2.4), fill=color)
        if tip:
            self.hits.append((0, 0, w, h, tip))

    def hover(self, x, y):
        for *_, tip in self.hits:
            return tip
        return None
