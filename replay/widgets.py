"""Replay-only widgets, drawn with DRAFT's tokens: the game timeline and
toggle chips (a chip row that can also be empty, unlike TabRow)."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from review.theme import anim
from review.theme.tokens import (ACCENT, BAD, BORDER, DIM, EDGE, EDGE_HI,
                                 F_SM, F_SM_B, F_XS, F_XS_B, FG, GOOD, GRID,
                                 HOVER, PANEL, PANEL2, TEAL_BG, mix)
from review.theme.widgets import px

from .model import BOOKMARK, DEATH, MISSED, fmt_time

AMBER = "#e0a84a"          # missed chances (between gold and red)
MARK_COLOR = {DEATH: BAD, MISSED: AMBER, BOOKMARK: FG}


class Chips(tk.Frame):
    """Toggle chips in a wrapping grid. ``value`` may be None (nothing
    picked); clicking the picked chip again clears it."""

    def __init__(self, master, options, value=None,
                 on_change: Callable[[Any], None] | None = None,
                 columns: int = 4, bg=PANEL, color=GOOD, active_bg=TEAL_BG):
        super().__init__(master, bg=bg)
        self.on_change, self.value = on_change, value
        self.color, self.active_bg = color, active_bg
        self.btns: dict[Any, tk.Label] = {}
        for i, (key, text) in enumerate(options):
            b = tk.Label(self, text=text, font=F_SM, padx=10, pady=4,
                         cursor="hand2", bg=PANEL2, fg=FG,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=BORDER)
            b.grid(row=i // columns, column=i % columns, sticky="ew",
                   padx=(0, px(4)), pady=(0, px(4)))
            b.bind("<Button-1>", lambda e, k=key: self.toggle(k))
            b.bind("<Enter>", lambda e, w=b, k=key: k != self.value
                   and anim.animate_wcol(w, "bg", HOVER), add="+")
            b.bind("<Leave>", lambda e, w=b, k=key: k != self.value
                   and anim.animate_wcol(w, "bg", PANEL2), add="+")
            self.btns[key] = b
        for c in range(min(columns, max(len(options), 1))):
            self.grid_columnconfigure(c, weight=1, uniform="chips")
        self.paint()

    def paint(self):
        for k, b in self.btns.items():
            on = k == self.value
            anim.stop_wcol(b, "bg", "fg")
            edge = self.color if on else BORDER
            b.configure(bg=self.active_bg if on else PANEL2,
                        fg=self.color if on else FG,
                        font=F_SM_B if on else F_SM,
                        highlightbackground=edge, highlightcolor=edge)

    def set(self, value, fire=False):
        self.value = value
        self.paint()
        if fire and self.on_change:
            self.on_change(value)

    def toggle(self, key):
        self.set(None if key == self.value else key, fire=True)


MAP_MAX = 14870.0


class MiniMap(tk.Canvas):
    """Summoner's Rift with one marker (where a death happened)."""

    def __init__(self, master, minimap=None, size=150, bg=PANEL):
        super().__init__(master, bg=bg, highlightthickness=0,
                         width=px(size), height=px(size))
        self.side = px(size)
        self._img = None
        s = self.side
        if minimap is not None:
            try:
                from PIL import Image, ImageEnhance, ImageTk
                im = minimap.convert("RGB").resize((s, s), Image.LANCZOS)
                im = ImageEnhance.Brightness(im).enhance(0.55)
                self._img = ImageTk.PhotoImage(im, master=self)
                self.create_image(0, 0, image=self._img, anchor="nw")
            except Exception:
                self._img = None
        if self._img is None:
            self.create_rectangle(0, 0, s, s, fill=PANEL2, width=0)
            self.create_line(0, s, s, 0, fill=GRID, width=px(6))  # mid lane
            self.create_line(s * .05, s * .95, s * .05, s * .05, s * .95,
                             s * .05, fill=GRID, width=px(3))      # top
            self.create_line(s * .05, s * .95, s * .95, s * .95, s * .95,
                             s * .05, fill=GRID, width=px(3))      # bot
            self.create_line(s * .2, s * .2, s * .8, s * .8,
                             fill=mix("#1b4a6e", PANEL2, .4), width=px(4))
        self.create_rectangle(0, 0, s - 1, s - 1, outline=EDGE)

    def mark(self, x, y, color=BAD):
        self.delete("mk")
        s = self.side
        X, Y = x / MAP_MAX * s, s - y / MAP_MAX * s
        for r, a in ((px(11), 0.75), (px(7), 0.45)):
            self.create_oval(X - r, Y - r, X + r, Y + r,
                             fill=mix(color, PANEL2, a), outline="",
                             tags="mk")
        r = px(4)
        self.create_oval(X - r, Y - r, X + r, Y + r, fill=color,
                         outline=FG, width=1, tags="mk")


class TimelineBar(tk.Canvas):
    """The whole game on one line. Lower track: your deaths (✕) and your
    own marks; upper track: kills/assists (teal) and objectives/towers
    (gold = your team, red = enemy). Gold line = where the replay is.

    Click a marker -> ``on_pick(key)``; click elsewhere -> ``on_seek(ms)``.
    """

    def __init__(self, master, tooltip=None, on_pick=None, on_seek=None,
                 bg=PANEL, height=96):
        super().__init__(master, bg=bg, highlightthickness=0, width=1,
                         height=px(height))
        self.tooltip, self.on_pick, self.on_seek = tooltip, on_pick, on_seek
        self.duration_ms = 1
        self.moments, self.marks = [], []
        self.selected = None
        self.playhead_ms: float | None = None
        self.cursor_ms: float | None = None
        self.hits: list[tuple[float, float, float, float, Any, str]] = []
        self._hover = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._tip(None))

    # data
    def set(self, duration_ms, moments, marks, selected=None):
        self.duration_ms = max(int(duration_ms), 1)
        self.moments, self.marks, self.selected = moments, marks, selected
        self.redraw()

    def select(self, key):
        self.selected = key
        self.redraw()

    def set_playhead(self, ms):
        if ms == self.playhead_ms:
            return
        self.playhead_ms = ms
        self._draw_heads()

    def set_cursor(self, ms):
        self.cursor_ms = ms
        self._draw_heads()

    # geometry
    def _box(self):
        w, h = self.winfo_width(), self.winfo_height()
        return px(10), w - px(10), h

    def _x(self, ms):
        x0, x1, _ = self._box()
        return x0 + (x1 - x0) * min(max(ms / self.duration_ms, 0), 1)

    def _ms(self, x):
        x0, x1, _ = self._box()
        return (x - x0) / max(x1 - x0, 1) * self.duration_ms

    # drawing
    # Layout (logical px, scaled by px()): label row, kills/objectives
    # track, label row, deaths/marks track, minute axis. Labels sit on their
    # own rows so markers never cover them.
    LBL1, TOP, LBL2, LOW, AXIS = 2, 25, 42, 64, 86

    def redraw(self):
        """The static picture is drawn with REVIEW's supersampled Pen (PIL,
        antialiased: Tk's canvas draws jagged diagonals and circles); the
        playhead stays a canvas item so it can move every second cheaply."""
        from PIL import ImageTk
        from review.theme.pen import Pen
        self.delete("all")
        self.hits = []
        x0, x1, h = self._box()
        if x1 - x0 < 20:
            return
        w = self.winfo_width()
        pen = Pen(w, h, bg=self["bg"], ss=3)
        top_y, low_y, axis_y = px(self.TOP), px(self.LOW), px(self.AXIS)
        lbl = mix(DIM, self["bg"], 0.15)
        # minute guides (faint, behind everything) + axis labels
        mins = self.duration_ms / 60_000
        step = 5 if mins <= 45 else 10
        m = 0
        while m <= mins:
            x = self._x(m * 60_000)
            if m:
                pen.line([(x, top_y - px(8)), (x, low_y + px(8))], GRID,
                         width=px(1), alpha=110)
            pen.text(x, axis_y, "%d" % m, F_XS, DIM,
                     anchor="mm" if m else "lm")
            m += step
        # tracks: thin, quiet
        for y in (top_y, low_y):
            pen.line([(x0, y), (x1, y)], GRID, width=px(1.5))
        pen.text(x0, px(self.LBL1), "KILLS · OBJECTIVES", F_XS_B, lbl,
                 anchor="lt")
        pen.text(x0, px(self.LBL2), "YOUR DEATHS · MARKS", F_XS_B, lbl,
                 anchor="lt")
        # upper track: objectives first, kills on top
        r = px(3.5)
        for mo in sorted((m for m in self.moments if m.kind != "death"),
                         key=lambda m: m.kind in ("kill", "assist")):
            x = self._x(mo.t_ms)
            if mo.kind == "kill":
                pen.circle(x, top_y, r, fill=GOOD)
            elif mo.kind == "assist":
                pen.circle(x, top_y, r - px(0.5), outline=GOOD,
                           width=px(1.5))
            else:
                col = ACCENT if mo.ours else BAD
                rr = r + px(1)
                if mo.kind == "tower":
                    col, rr = mix(col, self["bg"], 0.35), r
                pen.diamond(x, top_y, rr, fill=col)
            self.hits.append((x - r - 3, top_y - r - 4, x + r + 3,
                              top_y + r + 4, None,
                              "%s  %s" % (mo.time_text, mo.label)))
        # lower track: your deaths and marks
        R = px(5)
        from .model import KINDS, category_label
        for mk in self.marks:
            x = self._x(mk.t_ms)
            col = MARK_COLOR.get(mk.kind, FG)
            if self.selected == mk.id:
                pen.circle(x, low_y, R + px(4.5), fill=mix(EDGE_HI,
                                                         self["bg"], 0.82))
                pen.circle(x, low_y, R + px(4.5), outline=EDGE_HI,
                           width=px(1.5))
            if mk.kind == DEATH:
                c = col if mk.tagged else mix(col, self["bg"], 0.35)
                wd = px(2.2) if mk.tagged else px(1.8)
                pen.line([(x - R, low_y - R), (x + R, low_y + R)], c, wd)
                pen.line([(x - R, low_y + R), (x + R, low_y - R)], c, wd)
                for ex, ey in ((x - R, low_y - R), (x + R, low_y + R),
                               (x - R, low_y + R), (x + R, low_y - R)):
                    pen.circle(ex, ey, wd / 2, fill=c)      # round caps
                if not mk.tagged:
                    pen.circle(x + R + px(2.5), low_y - R - px(1), px(1.8),
                               fill=EDGE_HI)
            elif mk.kind == MISSED:
                tri = [(x, low_y - R - px(1)), (x + R + px(1), low_y + R),
                       (x - R - px(1), low_y + R)]
                pen.poly(tri, fill=col if mk.tagged else None,
                         outline=col, width=px(1.6))
            else:
                pen.rect(x - R + px(1), low_y - R + px(1), x + R - px(1),
                         low_y + R - px(1), fill=col)
            cat = category_label(mk.kind, mk.category)
            tip = "%s  %s%s%s" % (fmt_time(mk.t_ms / 1000),
                                  KINDS.get(mk.kind, mk.kind),
                                  (" · " + cat) if cat else
                                  (" · untagged" if mk.kind != BOOKMARK
                                   else ""),
                                  ("\n" + mk.note) if mk.note else "")
            self.hits.append((x - R - 4, low_y - R - 5, x + R + 4,
                              low_y + R + 5, mk.id, tip))
        self._photo = ImageTk.PhotoImage(pen.finish(), master=self)
        self.create_image(0, 0, image=self._photo, anchor="nw", tags="base")
        self._draw_heads()

    def _draw_heads(self):
        self.delete("head")
        _, _, h = self._box()
        y0, y1 = px(self.TOP - 10), px(self.LOW + 10)
        if self.cursor_ms is not None:
            x = round(self._x(self.cursor_ms))
            self.create_line(x, y0, x, y1, fill=DIM, dash=(2, 3),
                             tags="head")
        if self.playhead_ms is not None:
            x = round(self._x(self.playhead_ms))
            self.create_line(x, y0, x, y1, fill=EDGE_HI, width=px(1.5),
                             tags="head")
            self.create_text(x + px(5), px(self.LBL1), text=fmt_time(
                self.playhead_ms / 1000), fill=EDGE_HI, font=F_XS_B,
                anchor="nw", tags="head")

    # interaction
    def _hit(self, x, y):
        best = None
        for (a, b, c, d, key, tip) in self.hits:
            if a <= x <= c and b <= y <= d:
                if best is None or (key is not None and best[0] is None):
                    best = (key, tip)
        return best

    def _click(self, e):
        h = self._hit(e.x, e.y)
        if h and h[0] is not None:
            if self.on_pick:
                self.on_pick(h[0])
            return
        ms = self._ms(e.x)
        if 0 <= ms <= self.duration_ms and self.on_seek:
            self.on_seek(ms)

    def _motion(self, e):
        h = self._hit(e.x, e.y)
        self.configure(cursor="hand2")
        if h:
            self._tip((e.x_root, e.y_root, h[1]))
        else:
            self._tip(None)

    def _tip(self, spec):
        if self.tooltip is None:
            return
        key = spec[2] if spec else None
        if key == self._hover:
            return
        self._hover = key
        if spec:
            self.tooltip.show(*spec)
        else:
            self.tooltip.hide()
