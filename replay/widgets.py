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
                 bg=PANEL, height=86):
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
    def redraw(self):
        self.delete("all")
        self.hits = []
        x0, x1, h = self._box()
        if x1 - x0 < 20:
            return
        top_y, low_y = px(22), px(52)
        axis_y = h - px(14)
        # tracks
        for y in (top_y, low_y):
            self.create_line(x0, y, x1, y, fill=GRID, width=px(2))
        self.create_line(x0, axis_y - px(6), x1, axis_y - px(6), fill=EDGE)
        mins = self.duration_ms / 60_000
        step = 5 if mins <= 45 else 10
        m = 0
        while m <= mins:
            x = self._x(m * 60_000)
            self.create_line(x, axis_y - px(9), x, axis_y - px(3), fill=EDGE)
            self.create_text(x, axis_y + px(2), text="%d" % m, fill=DIM,
                             font=F_XS, anchor="n" if 0 < m else "nw")
            m += step
        self.create_text(x0, px(4), text="KILLS · OBJECTIVES", fill=DIM,
                         font=F_XS_B, anchor="nw")
        self.create_text(x0, low_y - px(16), text="YOUR DEATHS · MARKS",
                         fill=DIM, font=F_XS_B, anchor="nw")
        r = px(4)
        for mo in self.moments:
            if mo.kind == "death":
                continue
            x = self._x(mo.t_ms)
            if mo.kind in ("kill", "assist"):
                col = GOOD if mo.kind == "kill" else mix(GOOD, PANEL, 0.45)
                self.create_oval(x - r, top_y - r, x + r, top_y + r,
                                 fill=col, outline="")
            else:
                col = ACCENT if mo.ours else BAD
                if mo.kind == "tower":
                    col = mix(col, PANEL, 0.4)
                self.create_polygon(x, top_y - r - 1, x + r + 1, top_y,
                                    x, top_y + r + 1, x - r - 1, top_y,
                                    fill=col, outline="")
            self.hits.append((x - r - 2, top_y - r - 3, x + r + 2,
                              top_y + r + 3, None,
                              "%s  %s" % (mo.time_text, mo.label)))
        R = px(7)
        for mk in self.marks:
            x = self._x(mk.t_ms)
            col = MARK_COLOR.get(mk.kind, FG)
            sel = self.selected == mk.id
            if sel:
                self.create_rectangle(x - R - 3, low_y - R - 3, x + R + 3,
                                      low_y + R + 3, outline=EDGE_HI,
                                      width=2)
            if mk.kind == DEATH:
                w = px(3) if mk.tagged else px(2)
                c = col if mk.tagged else mix(col, PANEL, 0.25)
                self.create_line(x - R + 2, low_y - R + 2, x + R - 2,
                                 low_y + R - 2, fill=c, width=w)
                self.create_line(x - R + 2, low_y + R - 2, x + R - 2,
                                 low_y - R + 2, fill=c, width=w)
                if not mk.tagged:
                    self.create_oval(x + R - 3, low_y - R - 1, x + R + 1,
                                     low_y - R + 3, fill=EDGE_HI, outline="")
            elif mk.kind == MISSED:
                self.create_polygon(x, low_y - R, x + R, low_y + R - 1,
                                    x - R, low_y + R - 1,
                                    fill=col if mk.tagged else PANEL,
                                    outline=col, width=2)
            else:
                self.create_rectangle(x - R + 2, low_y - R + 2, x + R - 2,
                                      low_y + R - 2, fill=col, outline="")
            from .model import KINDS, category_label
            cat = category_label(mk.kind, mk.category)
            tip = "%s  %s%s%s" % (fmt_time(mk.t_ms / 1000),
                                  KINDS.get(mk.kind, mk.kind),
                                  (" · " + cat) if cat else
                                  (" · untagged" if mk.kind != BOOKMARK
                                   else ""),
                                  ("\n" + mk.note) if mk.note else "")
            self.hits.append((x - R - 2, low_y - R - 3, x + R + 2,
                              low_y + R + 3, mk.id, tip))
        self._draw_heads()

    def _draw_heads(self):
        self.delete("head")
        _, _, h = self._box()
        if self.cursor_ms is not None:
            x = self._x(self.cursor_ms)
            self.create_line(x, px(14), x, h - px(20), fill=DIM, dash=(2, 3),
                             tags="head")
        if self.playhead_ms is not None:
            x = self._x(self.playhead_ms)
            self.create_line(x, px(12), x, h - px(18), fill=EDGE_HI,
                             width=2, tags="head")
            self.create_polygon(x - px(5), px(8), x + px(5), px(8), x,
                                px(14), fill=EDGE_HI, outline="",
                                tags="head")
            self.create_text(x + px(7), px(4), text=fmt_time(
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
