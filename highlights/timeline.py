"""The editor's timeline: the whole game in three tracks, like an editing
program's timeline.

    KILLS       ● your kills · ○ assists · ✕ your deaths
    HIGHLIGHTS  one block per clip (length = clip, brightness = score;
                ✓ = saved); the selected one is outlined with handles
    OBJECTIVES  ◆ gold = your team · red = the enemy (small = towers)

Gold line = where the replay is. Click a block -> ``on_pick(i)``; drag
the selected block's edges to trim -> ``on_trim(i, in_ms, out_ms)`` on
release; click anything else -> ``on_seek(ms)``. While ``locked`` (a clip
is being recorded) clicks do nothing: no seeks during a recording.

The static picture is drawn with REVIEW's supersampled ``Pen``; the
selection, handles, recording progress and playhead are canvas items, so
dragging and the 1 s playhead updates stay cheap.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Any

from replay.model import fmt_time
from review.theme.tokens import (ACCENT, BAD, DIM, EDGE_HI, F_XS, F_XS_B,
                                 GOOD, GRID, PANEL, mix)
from review.theme.widgets import px

MIN_CLIP_MS = 2_000
SNAP_MS = 500


@dataclass
class Span:
    start_ms: int
    end_ms: int
    score: int
    title: str
    saved: bool = False


class ClipTimeline(tk.Canvas):
    GUTTER = 92                  # logical px for the track names
    RULER, KILLS, CLIPS, OBJ = 12, 40, 76, 112     # track centres
    CLIP_H = 13                  # half height of a clip block
    HEIGHT = 128

    def __init__(self, master, tooltip=None, on_pick=None, on_seek=None,
                 on_trim=None, bg=PANEL):
        super().__init__(master, bg=bg, highlightthickness=0, width=1,
                         height=px(self.HEIGHT))
        self.tooltip = tooltip
        self.on_pick, self.on_seek, self.on_trim = on_pick, on_seek, on_trim
        self.duration_ms = 1
        self.moments: list = []
        self.spans: list[Span] = []
        self.selected: int | None = None
        self.playhead_ms: float | None = None
        self.progress: float | None = None
        self.locked = False
        self.hits: list[tuple[float, float, float, float, Any, str]] = []
        self._drag: str | None = None          # "in" | "out"
        self._drag_ms: tuple[int, int] | None = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag_to)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._tip(None))

    # ------------------------------------------------------------ data
    def set(self, duration_ms, moments, spans: list[Span], selected=None):
        self.duration_ms = max(int(duration_ms), 1)
        self.moments, self.spans, self.selected = moments, spans, selected
        self.redraw()

    def select(self, i):
        self.selected = i
        self.redraw()

    def update_span(self, i, span: Span):
        self.spans[i] = span
        self.redraw()

    def set_playhead(self, ms):
        if ms == self.playhead_ms:
            return
        self.playhead_ms = ms
        self._draw_heads()

    def set_progress(self, frac: float | None):
        self.progress = frac
        self._draw_selection()

    # -------------------------------------------------------- geometry
    def _box(self):
        return px(self.GUTTER), self.winfo_width() - px(10)

    def x_of(self, ms):
        x0, x1 = self._box()
        return x0 + (x1 - x0) * min(max(ms / self.duration_ms, 0), 1)

    def ms_of(self, x):
        x0, x1 = self._box()
        return min(max((x - x0) / max(x1 - x0, 1), 0), 1) * self.duration_ms

    # --------------------------------------------------------- drawing
    def redraw(self):
        from PIL import ImageTk

        from review.theme.pen import Pen
        self.delete("all")
        self.hits = []
        x0, x1 = self._box()
        w, h = self.winfo_width(), self.winfo_height()
        if x1 - x0 < 40:
            return
        bg = self["bg"]
        pen = Pen(w, h, bg=bg, ss=3)
        ruler, ky, cy, oy = (px(v) for v in (self.RULER, self.KILLS,
                                              self.CLIPS, self.OBJ))
        lbl = mix(DIM, bg, 0.15)
        for y, name in ((ky, "KILLS"), (cy, "HIGHLIGHTS"), (oy, "OBJECTIVES")):
            pen.text(px(2), y, name, F_XS_B, lbl, anchor="lm")
            pen.line([(x0, y), (x1, y)], GRID, width=px(1.2))
        # ruler: minutes, a label every 5
        mins = self.duration_ms / 60_000
        m = 0
        while m <= mins:
            x = self.x_of(m * 60_000)
            big = m % 5 == 0
            pen.line([(x, ruler + px(5 if big else 8)), (x, ruler + px(11))],
                     DIM if big else GRID, width=px(1))
            if big:
                pen.line([(x, ky - px(10)), (x, oy + px(10))], GRID,
                         width=px(1), alpha=90)
                pen.text(x, ruler, "%d:00" % m if m else "0", F_XS, DIM,
                         anchor="mm" if m else "lm")
            m += 1
        self._draw_kills(pen, ky)
        self._draw_objectives(pen, oy)
        self._draw_spans(pen, cy)
        self._photo = ImageTk.PhotoImage(pen.finish(), master=self)
        self.create_image(0, 0, image=self._photo, anchor="nw", tags="base")
        self._draw_selection()
        self._draw_heads()

    def _draw_kills(self, pen, y):
        r = px(3.6)
        for mo in self.moments:
            x = self.x_of(mo.t_ms)
            if mo.kind == "kill":
                pen.circle(x, y, r, fill=GOOD)
            elif mo.kind == "assist":
                pen.circle(x, y, r - px(0.6), outline=GOOD, width=px(1.4))
            elif mo.kind == "death":
                k, wd = px(3.4), px(1.8)
                pen.line([(x - k, y - k), (x + k, y + k)], BAD, wd)
                pen.line([(x - k, y + k), (x + k, y - k)], BAD, wd)
            else:
                continue
            self.hits.append((x - r - 3, y - r - 4, x + r + 3, y + r + 4,
                              ("seek", mo.t_ms), "%s  %s" % (
                                  fmt_time(mo.t_ms / 1000), mo.label)))

    def _draw_objectives(self, pen, y):
        bg = self["bg"]
        for mo in self.moments:
            if mo.kind not in ("objective", "tower"):
                continue
            x = self.x_of(mo.t_ms)
            col = ACCENT if mo.ours else BAD
            r = px(4.2)
            if mo.kind == "tower":
                col, r = mix(col, bg, 0.4), px(3.2)
            pen.diamond(x, y, r + px(1.2), fill=bg)
            pen.diamond(x, y, r, fill=col)
            self.hits.append((x - r - 2, y - r - 4, x + r + 2, y + r + 4,
                              ("seek", mo.t_ms), "%s  %s" % (
                                  fmt_time(mo.t_ms / 1000), mo.label)))

    def _color(self, s: Span) -> str:
        if s.saved:
            return mix(GOOD, self["bg"], 0.45)
        f = min(s.score / 60, 1.0)
        return mix(ACCENT, self["bg"], 0.78 - 0.6 * f)

    def _draw_spans(self, pen, y):
        hh = px(self.CLIP_H)
        for i, s in enumerate(self.spans):
            a, b = self.x_of(s.start_ms), self.x_of(s.end_ms)
            b = max(b, a + px(3))
            pen.rect(a, y - hh, b, y + hh, fill=self._color(s))
            if b - a > px(22):
                pen.text((a + b) / 2, y, ("✓" if s.saved else str(s.score)),
                         F_XS_B, GOOD if s.saved else (
                             "#0a1428" if s.score >= 30 else EDGE_HI),
                         anchor="mm")
            self.hits.append((a - 1, y - hh - 2, b + 1, y + hh + 2, i,
                              "%s – %s  %s · score %d%s" % (
                                  fmt_time(s.start_ms / 1000),
                                  fmt_time(s.end_ms / 1000), s.title,
                                  s.score, " · saved" if s.saved else "")))

    def _draw_selection(self):
        """Selected clip: outline + in/out handles (+ recording progress)."""
        self.delete("sel")
        i = self.selected
        if i is None or not (0 <= i < len(self.spans)):
            return
        s = self.spans[i]
        a_ms, b_ms = self._drag_ms or (s.start_ms, s.end_ms)
        a, b = self.x_of(a_ms), self.x_of(b_ms)
        y, hh = px(self.CLIPS), px(self.CLIP_H)
        if self.progress is not None:
            self.create_rectangle(a, y - hh, a + (b - a) * self.progress,
                                  y + hh, fill=mix(BAD, self["bg"], 0.35),
                                  width=0, tags="sel")
        self.create_rectangle(a, y - hh - px(2), b, y + hh + px(2),
                              outline=EDGE_HI, width=px(2), tags="sel")
        hw = px(3)
        for x in (a, b):
            self.create_rectangle(x - hw, y - hh - px(5), x + hw,
                                  y + hh + px(5), fill=EDGE_HI, width=0,
                                  tags="sel")
        self.tag_raise("head")

    def _draw_heads(self):
        self.delete("head")
        if self.playhead_ms is None:
            return
        x = round(self.x_of(self.playhead_ms))
        self.create_line(x, px(self.RULER + 6), x, self.winfo_height(),
                         fill=EDGE_HI, width=px(1.5), tags="head")
        self.create_polygon(x - px(5), px(self.RULER - 4), x + px(5),
                            px(self.RULER - 4), x, px(self.RULER + 6),
                            fill=EDGE_HI, outline="", tags="head")
        self.create_text(x + px(8), px(self.RULER), text=fmt_time(
            self.playhead_ms / 1000), fill=EDGE_HI, font=F_XS_B,
            anchor="w", tags="head")

    # ---------------------------------------------------- interaction
    def _handle_at(self, x, y) -> str | None:
        i = self.selected
        if i is None or not (0 <= i < len(self.spans)):
            return None
        s = self.spans[i]
        cy, hh = px(self.CLIPS), px(self.CLIP_H) + px(6)
        if abs(y - cy) > hh:
            return None
        grab = px(6)
        a, b = self.x_of(s.start_ms), self.x_of(s.end_ms)
        if abs(x - b) <= grab:
            return "out"
        if abs(x - a) <= grab:
            return "in"
        return None

    def _hit(self, x, y):
        best = None
        for (a, b, c, d, key, tip) in self.hits:
            if a <= x <= c and b <= y <= d:
                best = (key, tip)
                if isinstance(key, int):       # clips win over markers
                    break
        return best

    def _press(self, e):
        if self.locked:
            return
        self._drag = self._handle_at(e.x, e.y)
        if self._drag:
            s = self.spans[self.selected]
            self._drag_ms = (s.start_ms, s.end_ms)
            return
        h = self._hit(e.x, e.y)
        if h and isinstance(h[0], int):
            if self.on_pick:
                self.on_pick(h[0])
            return
        if h and isinstance(h[0], tuple):
            if self.on_seek:
                self.on_seek(h[0][1])
            return
        x0, x1 = self._box()
        if x0 <= e.x <= x1 and self.on_seek:
            self.on_seek(self.ms_of(e.x))

    def _drag_to(self, e):
        if not self._drag or self._drag_ms is None:
            return
        a, b = self._drag_ms
        ms = round(self.ms_of(e.x) / SNAP_MS) * SNAP_MS
        if self._drag == "in":
            a = int(min(max(ms, 0), b - MIN_CLIP_MS))
        else:
            b = int(max(min(ms, self.duration_ms), a + MIN_CLIP_MS))
        self._drag_ms = (a, b)
        self._draw_selection()
        self._tip_at(e, "%s – %s  (%d s)" % (fmt_time(a / 1000),
                                             fmt_time(b / 1000),
                                             round((b - a) / 1000)))

    def _release(self, e):
        if not self._drag:
            return
        a, b = self._drag_ms
        i = self.selected
        self._drag = None
        self._drag_ms = None
        if self.on_trim and i is not None:
            self.on_trim(i, a, b)

    def _motion(self, e):
        if self._drag:
            return
        if self._handle_at(e.x, e.y) and not self.locked:
            self.configure(cursor="sb_h_double_arrow")
            self._tip(None)
            return
        h = self._hit(e.x, e.y)
        self.configure(cursor="hand2" if h and not self.locked else "")
        if h:
            self._tip_at(e, h[1])
        else:
            self._tip(None)

    def _tip_at(self, e, text):
        if self.tooltip:
            self.tooltip.show(e.x_root, e.y_root, text)

    def _tip(self, _):
        if self.tooltip:
            self.tooltip.hide()
