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


GROUP_MS = 15_000         # events this close in time belong together
GROUP_SPAN_MS = 40_000    # ...but one group never covers more than this
STACK = 3                 # icons shown per group; the rest is "+N"


def moment_row(mo) -> str:
    """Upper track rows: 'me' (your kills/assists, on the line), 'ours'
    (your team's objectives/towers, above), 'theirs' (enemy's, below)."""
    if mo.kind in ("kill", "assist"):
        return "me"
    return "ours" if mo.ours else "theirs"


def moment_rank(mo) -> int:
    """Lower = more important (drawn nearest the line, listed first)."""
    lab = mo.label.lower()
    if mo.kind == "objective":
        return 0 if ("baron" in lab or "elder" in lab) else 1
    if mo.kind == "kill":
        return 2
    if mo.kind == "assist":
        return 3
    return 4 if "inhibitor" in lab else 5


def group_moments(moments, x_of, min_gap: float):
    """Bundle the upper track's moments so nothing overlaps.

    Per row, events in time order join the open group when they are within
    ``GROUP_MS`` of its last event (and ``GROUP_SPAN_MS`` of its first), or
    when they would be drawn less than ``min_gap`` px from it. Returns
    ``[(row, x, items)]``: x is the first event's position, items are
    sorted most important first."""
    out = []
    for row in ("me", "ours", "theirs"):
        cur = None
        for mo in sorted((m for m in moments if m.kind != "death"
                          and moment_row(m) == row), key=lambda m: m.t_ms):
            x = x_of(mo.t_ms)
            if cur is not None and (
                    (mo.t_ms - cur[2][-1].t_ms <= GROUP_MS
                     and mo.t_ms - cur[2][0].t_ms <= GROUP_SPAN_MS)
                    or x - cur[1] < min_gap):
                cur[2].append(mo)
                continue
            cur = [row, x, [mo]]
            out.append(cur)
    return [(r, x, sorted(it, key=lambda m: (moment_rank(m), m.t_ms)))
            for r, x, it in out]


def group_tip(items, limit: int = 8) -> str:
    lines = ["%s  %s" % (m.time_text, m.label)
             for m in sorted(items, key=lambda m: m.t_ms)]
    if len(lines) > limit:
        lines = lines[:limit] + ["… %d more" % (len(lines) - limit)]
    return "\n".join(lines)


class TimelineBar(tk.Canvas):
    """The whole game on one line. Lower track: your deaths (✕) and your
    own marks. Upper track: your kills/assists (teal) on the line, your
    team's objectives/towers (gold) stacked above it, the enemy's (red)
    stacked below. Events close in time are one group: up to three icons,
    then "+N"; hover lists them all, click jumps to the first. Gold line =
    where the replay is.

    Click a marker -> ``on_pick(key)``; click elsewhere -> ``on_seek(ms)``.
    """

    def __init__(self, master, tooltip=None, on_pick=None, on_seek=None,
                 bg=PANEL, height=134):
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
        self._resize_job = None
        self._drawn_size = None
        self.bind("<Configure>", self._resized)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._tip(None))

    def _resized(self, e):
        """Redraw once the size settles: the antialiased picture costs a
        few ms, and a window drag sends dozens of <Configure>s."""
        if (e.width, e.height) == self._drawn_size:
            return
        if self._drawn_size is None:          # first layout: draw now
            self.redraw()
            return
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._settled)

    def _settled(self):
        self._resize_job = None
        if self.winfo_exists():
            self.redraw()

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
    # own rows so markers never cover them. The upper track's stacks reach
    # STACK_IN + (STACK - 1) * STACK_STEP above and below TOP.
    LBL1, TOP, LBL2, LOW, AXIS = 2, 46, 80, 102, 124
    STACK_IN, STACK_STEP = 9, 8

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
        self._drawn_size = (w, h)
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
                pen.line([(x, top_y - px(28)), (x, low_y + px(8))], GRID,
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
        self._draw_moments(pen, top_y)
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

    def _draw_moments(self, pen, top_y):
        """Upper track, grouped (see ``group_moments``)."""
        bg = self["bg"]
        r = px(3.5)
        label_w = px(13)
        gap = 2 * r + px(5) + label_w
        step, inner = px(self.STACK_STEP), px(self.STACK_IN)
        for row, x, items in group_moments(self.moments, self._x, gap):
            n = len(items)
            if row == "me":
                kill = any(m.kind == "kill" for m in items)
                if kill:
                    pen.circle(x, top_y, r, fill=GOOD)
                else:
                    pen.circle(x, top_y, r - px(0.5), outline=GOOD,
                               width=px(1.5))
                ys = [top_y]
                if n > 1:
                    pen.text(x + r + px(2), top_y, "×%d" % n, F_XS_B, GOOD,
                             anchor="lm")
            else:
                sign = -1 if row == "ours" else 1
                shown = items[:STACK]
                ys = [top_y + sign * (inner + i * step)
                      for i in range(len(shown))]
                # outermost first, so the most important sits on top
                for mo, y in reversed(list(zip(shown, ys))):
                    col = ACCENT if mo.ours else BAD
                    rr = r + px(1)
                    if mo.kind == "tower":
                        col, rr = mix(col, bg, 0.35), r
                    pen.diamond(x, y, rr + px(1.2), fill=bg)   # separates
                    pen.diamond(x, y, rr, fill=col)
                if n > STACK:
                    pen.text(x + r + px(3), ys[-1], "+%d" % (n - STACK),
                             F_XS_B, DIM, anchor="lm")
            y_lo, y_hi = min(ys), max(ys)
            right = label_w if (n > STACK or (row == "me" and n > 1)) else 0
            self.hits.append((x - r - 3, y_lo - r - 3, x + r + 3 + right,
                              y_hi + r + 3, ("seek", items and min(
                                  m.t_ms for m in items)),
                              group_tip(items)))

    def _draw_heads(self):
        self.delete("head")
        _, _, h = self._box()
        y0, y1 = px(self.TOP - 30), px(self.LOW + 10)
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
        if h and isinstance(h[0], tuple) and h[0][0] == "seek":
            if self.on_seek:
                self.on_seek(h[0][1])
            return
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


class MomentList(tk.Canvas):
    """The Moments list drawn on one canvas (like REVIEW's Table): a row
    per mark with symbol, time, label and tag. It used to be ~6 widgets a
    row; on Windows each one is a native window, and building, laying out
    and destroying ~90 of them made every game switch stall for ~200 ms.

    ``set(rows, selected)``; rows are ``MomentRow``s. Click -> on_pick(id).
    """

    def __init__(self, master, on_pick: Callable[[Any], None], bg=PANEL):
        from tkinter import font as tkfont
        super().__init__(master, bg=bg, highlightthickness=0, height=1,
                         cursor="hand2")
        self.on_pick, self.bg = on_pick, bg
        self.rows: list = []
        self.selected = None
        self.row_h = px(26)
        self._f = tkfont.Font(font=F_SM)
        self._f_chip = tkfont.Font(font=F_XS)
        self._f_chip_b = tkfont.Font(font=F_XS_B)
        self._hover = -1
        self._drawn_w = None
        self.bind("<Configure>", lambda e: e.width != self._drawn_w
                  and self.redraw())
        self.bind("<Motion>", lambda e: self._set_hover(self._row(e.y)))
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.bind("<Button-1>", self._click)

    def set(self, rows, selected=None):
        self.rows, self.selected = rows, selected
        self.configure(height=max(len(rows), 1) * self.row_h)
        self.redraw()

    def select(self, key):
        self.selected = key
        self.redraw()

    def _row(self, y) -> int:
        i = int(y // self.row_h)
        return i if 0 <= i < len(self.rows) else -1

    def _fit(self, font, text: str, avail: float) -> str:
        if font.measure(text) <= avail:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid].rstrip() + "…") <= avail:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + "…" if lo else ""

    def _row_bg(self, i) -> str:
        if self.rows[i].key == self.selected:
            return HOVER
        return mix(PANEL, HOVER, 0.45) if i == self._hover else self.bg

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:
            return
        self._drawn_w = w
        rh, f = self.row_h, self._f
        chw = f.measure("0")
        x_sym, x_time = px(4) + chw, px(4) + 2 * chw + px(4)
        x_lab = x_time + 6 * chw + px(4)
        for i, r in enumerate(self.rows):
            y0, cy = i * rh, i * rh + rh / 2
            sel = r.key == self.selected
            self.create_rectangle(0, y0 + 1, w, y0 + rh - 1,
                                  fill=self._row_bg(i), width=0,
                                  tags=("bg", "bg%d" % i))
            if sel:
                self.create_rectangle(0, y0 + 1, px(3), y0 + rh - 1,
                                      fill=EDGE_HI, width=0)
            self.create_text(x_sym, cy, text=r.sym, fill=r.sym_color,
                             font=F_SM_B)
            self.create_text(x_time, cy, text=r.time, fill=FG, font=F_SM,
                             anchor="w")
            chip_w = 0
            if r.chip:
                cf = self._f_chip_b if r.chip_bold else self._f_chip
                chip_w = cf.measure(r.chip)
                self.create_text(w - px(6), cy, text=r.chip,
                                 fill=r.chip_color, anchor="e",
                                 font=F_XS_B if r.chip_bold else F_XS)
            avail = w - px(6) - chip_w - px(10) - x_lab
            self.create_text(x_lab, cy, text=self._fit(f, r.label, avail),
                             fill=FG if sel else DIM, font=F_SM, anchor="w")

    def _set_hover(self, i):
        if i == self._hover:
            return
        old, self._hover = self._hover, i
        for j in (old, i):
            if 0 <= j < len(self.rows):
                self.itemconfigure("bg%d" % j, fill=self._row_bg(j))

    def _click(self, e):
        i = self._row(e.y)
        if i >= 0 and self.on_pick:
            self.on_pick(self.rows[i].key)


class MomentRow:
    __slots__ = ("key", "sym", "sym_color", "time", "label", "chip",
                 "chip_color", "chip_bold")

    def __init__(self, key, sym, sym_color, time, label, chip, chip_color,
                 chip_bold):
        self.key, self.sym, self.sym_color, self.time = key, sym, sym_color, time
        self.label, self.chip = label, chip
        self.chip_color, self.chip_bold = chip_color, chip_bold
