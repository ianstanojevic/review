"""DRAFT's widgets, shared: window chrome, CutPanel, banner, tab rows,
tables, bars, tooltips. Visual values come from tokens.py only.

Sizes are in logical px and scaled by ``px()`` (Tk scales fonts to the real
DPI but not pixels — DRAFT does the same with ``self.s``).
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import font as tkfont
from typing import Any, Callable

from . import anim
from .tokens import (ACCENT, ACCENT_HI, BAD, BORDER, BTN_FG, DARK_BG, DIM,
                     DMG_AD, DMG_AP, DMG_TRUE, EDGE, EDGE_HI, F_MD, F_SM,
                     F_SM_B, F_XS, F_XS_B, FG, GOOD, HOVER, PANEL, PANEL2,
                     SP_L, SP_M, SP_S, SP_XS, TEAL_BG, TITLE_FONT, TOP_BG)

SCALE = 1.0


def px(n: float) -> int:
    return int(round(n * SCALE))


# ------------------------------------------------------------ window chrome

def _dpi_setup() -> float:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        import os
        forced = os.environ.get("REVIEW_SCALE", "").strip()
        if forced:            # testing: render as on a 100/125/150 % screen
            return float(forced)
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def diamond_icon(size: int = 32) -> tk.PhotoImage:
    """Window icon: the hextech diamond (same as DRAFT)."""
    img = tk.PhotoImage(width=size, height=size)
    c = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            d = abs(x - c) + abs(y - c)
            if d <= c:
                if d >= c - 2.5:
                    img.put(EDGE_HI, (x, y))
                elif d <= 4:
                    img.put(ACCENT, (x, y))
                else:
                    img.put(DARK_BG, (x, y))
    return img


def enable_dark_titlebar(root) -> None:
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def make_root(title: str) -> tk.Tk:
    """A Tk root dressed like DRAFT: DPI-aware, dark title bar, diamond
    icon, invisible until ``anim.fade_in(root)``."""
    global SCALE
    scale = _dpi_setup()
    root = tk.Tk()
    SCALE = max(1.0, scale)
    try:
        root.tk.call("tk", "scaling", SCALE * 96.0 / 72.0)
    except tk.TclError:
        pass
    root.title(title)
    root.configure(bg=DARK_BG)
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    # REVIEW's own radar icon (DRAFT keeps the diamond); several sizes so
    # Windows picks a sharp one for the title bar and the taskbar.
    # default=True: dialogs (Settings, benchmark) inherit it.
    try:
        from PIL import ImageTk
        from .icon import icon_image
        root._icon = [ImageTk.PhotoImage(icon_image(s), master=root)
                      for s in (64, 48, 32, 16)]
        root.iconphoto(True, *root._icon)
    except Exception:
        root._icon = diamond_icon()
        root.iconphoto(True, root._icon)
    enable_dark_titlebar(root)
    root.option_add("*Font", F_SM)
    return root


def char_w(font=F_SM) -> int:
    return tkfont.Font(font=font).measure("0")


# --------------------------------------------------------------- CutPanel

class CutPanel(tk.Frame):
    """Hextech panel: notched corners (top-left, bottom-right), gold outline
    with brighter gold accents on the bevels. Content goes in ``.inner``."""

    def __init__(self, master, cut=12, padx=10, pady=8, grow=False,
                 fill=False):
        super().__init__(master, bg=DARK_BG)
        self.cut, self.pad = cut, (padx, pady)
        self.canvas = tk.Canvas(self, bg=DARK_BG, highlightthickness=0, bd=0)
        if fill:
            self.canvas.configure(width=1)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self._win = self.canvas.create_window(padx + 4, pady + 4,
                                              window=self.inner, anchor="nw")
        if grow:
            self.canvas.bind("<Configure>", self._grow_fit, add="+")
        elif fill:
            self.canvas.bind("<Configure>", self._fill_width, add="+")
            self.inner.bind("<Configure>", self._fit_height)
        else:
            self.inner.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._redraw, add="+")

    def _fit(self, e):
        self.canvas.configure(width=e.width + 2 * self.pad[0] + 8,
                              height=e.height + 2 * self.pad[1] + 8)

    def _fill_width(self, e):
        self.canvas.itemconfigure(
            self._win, width=max(e.width - 2 * self.pad[0] - 8, 10))

    def _fit_height(self, e):
        self.canvas.configure(height=e.height + 2 * self.pad[1] + 8)

    def _grow_fit(self, e):
        self.canvas.itemconfigure(
            self._win, width=max(e.width - 2 * self.pad[0] - 8, 10),
            height=max(e.height - 2 * self.pad[1] - 8, 10))

    def _redraw(self, e):
        c, k = self.canvas, self.cut
        w, h = e.width, e.height
        c.delete("border")
        pts = [k, 1, w - 2, 1, w - 2, h - k - 1, w - k - 1, h - 2,
               1, h - 2, 1, k]
        c.create_polygon(pts, fill=PANEL, outline=EDGE, tags="border")
        c.create_line(k + 1, 1, 1, k + 1, fill=EDGE_HI, width=2, tags="border")
        c.create_line(w - 2, h - k - 2, w - k - 2, h - 2, fill=EDGE_HI,
                      width=2, tags="border")
        c.tag_lower("border")


def hide_when_tight(row, left, right, gap=12):
    """A header row with a title on the left and a note on the right: when
    the row is too narrow for both, hide the note instead of letting it
    print over the title (the full note stays in the tooltip/PNG)."""
    def check(e=None):
        w = row.winfo_width()
        if w <= 1:
            return
        need = left.winfo_reqwidth() + right.winfo_reqwidth() + px(gap)
        shown = right.winfo_manager() == "pack"
        if need > w and shown:
            right.pack_forget()
        elif need <= w and not shown and right.cget("text"):
            right.pack(side="right")
    row.bind("<Configure>", check, add="+")
    right.bind("<Configure>", lambda e: row.after_idle(check), add="+")
    return check


def section_title(parent, text: str, color: str = EDGE_HI,
                  bg: str = PANEL, right: str | None = None) -> tk.Label | None:
    """Coloured section header with an underline rule ("MY TEAM" style).
    Returns the right-hand label (for n / status text) if requested."""
    row = tk.Frame(parent, bg=bg)
    row.pack(fill="x")
    title = tk.Label(row, text=text, bg=bg, fg=color, font=F_SM_B)
    title.pack(side="left")
    rlbl = None
    if right is not None:
        rlbl = tk.Label(row, text=right, bg=bg, fg=DIM, font=F_XS)
        rlbl.pack(side="right")
        hide_when_tight(row, title, rlbl)
    tk.Frame(parent, bg=color, height=2).pack(fill="x", pady=(SP_XS, SP_M))
    return rlbl


def caption(parent, text: str, bg: str = PANEL, pady=(SP_L, SP_XS)):
    lbl = tk.Label(parent, text=text, bg=bg, fg=DIM, font=F_XS_B)
    lbl.pack(anchor="w", pady=pady)
    return lbl


def vsep(parent, bg=DARK_BG):
    tk.Frame(parent, bg=EDGE, width=1).pack(side="left", fill="y",
                                            padx=SP_L, pady=SP_XS)


# ----------------------------------------------------------------- banner

class Banner(tk.Canvas):
    """Title in wide-spaced gold serif caps, gold rule with diamond end-caps,
    a context label on the right and the settings gear."""

    def __init__(self, master, title: str, on_gear: Callable | None = None):
        super().__init__(master, bg=DARK_BG, highlightthickness=0,
                         height=px(42))
        self.title = " ".join(title.upper())
        self.context = ""
        self.on_gear = on_gear
        self.bind("<Configure>", lambda e: self.redraw())

    def set_context(self, text: str) -> None:
        self.context = text
        self.redraw()

    def redraw(self) -> None:
        c = self
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        cy = h // 2
        t = c.create_text(2, cy, text=self.title, anchor="w", fill=EDGE_HI,
                          font=TITLE_FONT)
        x0 = c.bbox(t)[2] + 16
        gear = c.create_text(w - 2, cy, text="⚙", anchor="e", fill=DIM,
                             font=("Segoe UI", 13))
        if self.on_gear:
            c.tag_bind(gear, "<Button-1>", lambda e: self.on_gear())
        c.tag_bind(gear, "<Enter>", lambda e: (
            c.itemconfigure(gear, fill=EDGE_HI), c.configure(cursor="hand2")))
        c.tag_bind(gear, "<Leave>", lambda e: (
            c.itemconfigure(gear, fill=DIM), c.configure(cursor="")))
        x1 = c.bbox(gear)[0] - 12
        if self.context:
            pt = c.create_text(x1, cy, text=self.context, anchor="e",
                               fill=DIM, font=("Segoe UI", 9))
            x1 = c.bbox(pt)[0] - 14
        if x1 - x0 > 40:
            c.create_line(x0, cy, x1, cy, fill=EDGE)
            c.create_line(x0, cy, min(x0 + 80, x1), cy, fill=EDGE_HI, width=2)
            r = max(4, px(4))
            for x in (x0, x1):
                c.create_polygon(x, cy - r, x + r, cy, x, cy + r, x - r, cy,
                                 fill=ACCENT, outline="")
        c.create_line(0, h - 1, w, h - 1, fill=EDGE)


# --------------------------------------------------------------- tab rows

class TabRow(tk.Frame):
    """A caps caption ("ROLE") followed by segmented buttons.

    style="gold": active is gold-filled, dark text, ◆ prefix (DRAFT's ROLE).
    style="teal": active is dark-teal with teal text (DRAFT's TIER).
    """

    def __init__(self, master, label: str | None, options, value=None,
                 on_change: Callable[[Any], None] | None = None,
                 style: str = "gold", bg: str = DARK_BG, small: bool = False,
                 icons: dict | None = None):
        super().__init__(master, bg=bg)
        self.style, self.on_change, self.bg = style, on_change, bg
        self.small = small
        # key -> (icon when inactive, icon when active)
        self.icons = icons or {}
        if label:
            tk.Label(self, text=label, bg=bg, fg=DIM, font=F_XS_B).pack(
                side="left", padx=(0, SP_M))
        self.btns: dict[Any, tk.Label] = {}
        self.texts: dict[Any, str] = {}
        self.value = value
        self.set_options(options, value)

    def set_options(self, options, value=None) -> None:
        for b in self.btns.values():
            b.destroy()
        self.btns.clear()
        self.texts.clear()
        for key, text in options:
            font = (F_XS_B if self.small else F_SM_B) if self.style == "gold" \
                else (F_XS if self.small else F_SM)
            # raw pixels, exactly like DRAFT's ROLE/TIER pills
            b = tk.Label(self, text=text, padx=10 if self.style == "gold"
                         else 8, pady=3 if self.small else 4, font=font,
                         cursor="hand2", bg=PANEL2, fg=DIM)
            if self.small:
                # in-panel rows (STAT, PHASE, OVERLAY…): framed buttons with
                # readable text, so they read as buttons, not as labels
                b.configure(font=F_SM, padx=10, pady=4, fg=FG,
                            highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor=BORDER)
            b.pack(side="left", padx=SP_XS)
            b.bind("<Button-1>", lambda e, k=key: self.select(k))
            b.bind("<Enter>", lambda e, w=b, k=key: k != self.value
                   and anim.animate_wcol(w, "bg", HOVER), add="+")
            b.bind("<Leave>", lambda e, w=b, k=key: k != self.value
                   and anim.animate_wcol(w, "bg", PANEL2), add="+")
            self.btns[key] = b
            self.texts[key] = text
        if value is not None:
            self.value = value
        if self.value not in self.btns and self.btns:
            self.value = next(iter(self.btns))
        self.paint()

    def paint(self) -> None:
        for k, b in self.btns.items():
            on = k == self.value
            anim.stop_wcol(b, "bg", "fg")
            ic = self.icons.get(k)
            if ic:
                b.configure(image=ic[1] if on else ic[0], compound="left")
            if self.style == "gold":
                b.configure(bg=ACCENT if on else PANEL2,
                            fg=BTN_FG if on else (FG if self.small else DIM),
                            text=(" " + self.texts[k]) if ic else
                            (("◆ " + self.texts[k]) if on else self.texts[k]))
            else:
                b.configure(bg=TEAL_BG if on else PANEL2,
                            fg=GOOD if on else (FG if self.small else DIM))
            if self.small:
                edge = (ACCENT if self.style == "gold" else GOOD) if on                     else BORDER
                b.configure(highlightbackground=edge, highlightcolor=edge,
                            font=F_SM_B if on else F_SM)

    def select(self, key, fire: bool = True) -> None:
        changed = key != self.value
        self.value = key
        self.paint()
        if fire and changed and self.on_change:
            self.on_change(key)


def button(parent, text: str, cmd: Callable, fg: str = FG, bg: str = PANEL2,
           font=F_MD, gold: bool = False) -> tk.Label:
    """DRAFT action button ("Suggest bans", "Clear")."""
    if gold:
        b = tk.Label(parent, text=text, bg=ACCENT, fg=BTN_FG, font=F_MD_B,
                     padx=14, pady=6, cursor="hand2")      # DRAFT: raw px
        anim.hoverfx(b, ACCENT_HI)
    else:
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=font, padx=12,
                     pady=6, cursor="hand2")                # DRAFT: raw px
        anim.hoverfx(b, HOVER, FG)
    b.bind("<Button-1>", lambda e: cmd())
    return b


F_MD_B = ("Segoe UI", 10, "bold")


def entry(parent, width: int = 28, font=F_MD) -> tk.Entry:
    e = tk.Entry(parent, bg=PANEL2, fg=FG, insertbackground=EDGE_HI,
                 relief="flat", font=font, width=width,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=EDGE_HI, disabledbackground=PANEL2,
                 selectbackground=EDGE, selectforeground=FG)
    return e


# ---------------------------------------------------------------- tooltip

class Tooltip:
    """One floating tooltip per app (gold hairline frame, PANEL body)."""

    def __init__(self, root):
        self.root = root
        self.win: tk.Toplevel | None = None

    def hide(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    def show(self, x_root: int, y_root: int, text: str | None = None,
             build: Callable[[tk.Frame], None] | None = None) -> None:
        self.hide()
        t = tk.Toplevel(self.root)
        self.win = t
        t.overrideredirect(True)
        t.configure(bg=EDGE)
        try:
            t.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        body = tk.Frame(t, bg=PANEL, padx=px(8), pady=px(5))
        body.pack(padx=1, pady=1)
        if build:
            build(body)
        else:
            tk.Label(body, text=text, bg=PANEL, fg=FG, font=F_XS,
                     wraplength=px(300), justify="left").pack()
        t.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = min(x_root + 14, sw - t.winfo_reqwidth() - 8)
        y = y_root + 16
        if y + t.winfo_reqheight() > sh - 8:
            y = y_root - t.winfo_reqheight() - 10
        t.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        anim.fade_in(t)

    def bind(self, widget, text) -> None:
        """Hover tooltip; ``text`` may be a callable returning text."""
        def show(e):
            txt = text() if callable(text) else text
            if txt:
                self.show(e.x_root, e.y_root, txt)
        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")


# ------------------------------------------------------------ scroll area

class SlimScroll(tk.Canvas):
    """Slim hextech scrollbar (native tk.Scrollbar can't be coloured)."""

    def __init__(self, master, target, width=6):
        super().__init__(master, width=px(width), bg=PANEL2,
                         highlightthickness=0)
        self.target = target
        self.lo, self.hi = 0.0, 1.0
        target.configure(yscrollcommand=self._thumb)
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Configure>", lambda e: self._thumb(self.lo, self.hi))

    def _thumb(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self.delete("thumb")
        if self.hi - self.lo >= 1.0:
            return
        h = self.winfo_height()
        self.create_rectangle(0, self.lo * h, px(8), self.hi * h,
                              fill=EDGE_HI, outline="", tags="thumb")

    def _drag(self, e):
        if self.hi - self.lo >= 1.0:
            return
        h = max(self.winfo_height(), 1)
        span = self.hi - self.lo
        self.target.yview_moveto(min(max(e.y / h - span / 2, 0.0), 1.0))


import weakref

_TABLES: "weakref.WeakSet" = weakref.WeakSet()


def _page_scrolled():
    for t in list(_TABLES):
        try:
            if t.winfo_exists() and t.winfo_ismapped():
                t._ensure_rows()
        except tk.TclError:
            pass


class ScrollArea(tk.Frame):
    """A vertically scrolling frame (``.body``) with a SlimScroll."""

    def __init__(self, master, bg=DARK_BG):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = SlimScroll(self, self.canvas)
        self.bar.pack(side="left", fill="y", padx=(px(4), 0))
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.body,
                                              anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=(0, 0, e.width, e.height)))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        self.canvas._scrollable = True
        self.canvas._smooth = False      # see install_wheel
        thumb = self.canvas.cget("yscrollcommand")

        def on_scroll(first, last, _t=thumb):
            if _t:
                self.canvas.tk.call(_t, first, last)
            _page_scrolled()             # lazily drawn tables catch up
        self.canvas.configure(yscrollcommand=on_scroll)

    def to_top(self):
        self.canvas.yview_moveto(0)

    def fresh_body(self) -> tk.Frame:
        """A new, not yet shown body to build a page into (see swap_body)."""
        return tk.Frame(self.canvas, bg=self.canvas["bg"])

    def swap_body(self, new: tk.Frame) -> tk.Frame:
        """Show ``new`` in place of the current body, in one step, keeping
        the scroll position. Returns the old body (hidden, not destroyed)."""
        old, old_win = self.body, self._win
        self.body = new
        self._win = self.canvas.create_window(
            0, 0, window=new, anchor="nw",
            width=max(self.canvas.winfo_width(), 1))
        new.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=(0, 0, e.width, e.height)))
        self.canvas.delete(old_win)
        return old


def install_wheel(root) -> None:
    """Smooth wheel scrolling for whatever scrollable canvas is under the
    pointer (ScrollArea or Table), like DRAFT's eased scroll."""
    def wheel(e):
        try:
            w = root.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        # innermost scrollable that actually has something to scroll
        while w is not None:
            if getattr(w, "_scrollable", False):
                lo, hi = w.yview()
                if hi - lo < 1.0:
                    break
            w = getattr(w, "master", None)
        if w is None:
            return
        lo, hi = w.yview()
        if not getattr(w, "_smooth", True):
            # pages full of widgets: one jump per notch. Easing moved every
            # embedded label ~10 times per notch, which made text trail
            # behind the charts ("laggy text")
            step = (hi - lo) * 0.14
            w.yview_moveto(max(0.0, min(lo - step if e.delta > 0
                                        else lo + step, 1.0 - (hi - lo))))
            return
        base = getattr(w, "_scr_target", None)
        base = lo if base is None else base
        step = (hi - lo) * 0.18
        target = base - step if e.delta > 0 else base + step
        w._scr_target = max(0.0, min(target, 1.0 - (hi - lo)))
        if getattr(w, "_scr_job", None) is None:
            tick(w)

    def tick(c):
        if not c.winfo_exists():
            return
        lo, _ = c.yview()
        d = c._scr_target - lo
        if abs(d) < 0.0005:
            c.yview_moveto(c._scr_target)
            c._scr_job = None
            c._scr_target = None
            return
        c.yview_moveto(lo + d * 0.35)
        c._scr_job = c.after(anim.FRAME_MS, lambda: tick(c))
    root.bind_all("<MouseWheel>", wheel)


# ----------------------------------------------------------------- meters

class SplitBar(tk.Canvas):
    """DRAFT's WIN CHANCE bar: teal share vs red share with a centre tick."""

    def __init__(self, master, width=52, height=10, bg=PANEL2):
        super().__init__(master, bg=bg, highlightthickness=0,
                         width=px(width), height=px(height))
        self.frac = None
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, frac: float | None):
        self.frac = frac
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if self.frac is None or w <= 1:
            return
        x = int(w * max(0.0, min(1.0, self.frac)))
        self.create_rectangle(0, 0, x, h, fill=GOOD, width=0)
        self.create_rectangle(x, 0, w, h, fill=BAD, width=0)
        self.create_line(w // 2, 0, w // 2, h, fill=DARK_BG)


class DmgBar(tk.Canvas):
    """Physical / magic / true damage composition bar (DRAFT colours)."""

    def __init__(self, master, height=20, mini=False, bg=PANEL2):
        super().__init__(master, bg=bg, highlightthickness=0, width=1,
                         height=px(height))
        self.mix = (0.0, 0.0, 0.0)
        self.mini = mini
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, mix):
        self.mix = tuple(0.0 if v != v else v for v in mix)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        total = sum(self.mix)
        if w <= 1 or total <= 0:
            return
        x = 0.0
        for val, col in zip(self.mix, (DMG_AD, DMG_AP, DMG_TRUE)):
            seg = w * val / total
            if seg < 0.5:
                continue
            self.create_rectangle(x, 0, x + seg, h, fill=col, outline="")
            if seg > px(30) and not self.mini:
                self.create_text(x + seg / 2, h // 2, text="%.0f%%" % (
                    100 * val / total), fill=BTN_FG, font=F_XS_B)
            x += seg


class Tiles(tk.Canvas):
    """DRAFT's TEAM SCALING cells: equal cells, small label on top, bold
    coloured value below, thin dividers. cells = [(label, value, colour)]."""

    def __init__(self, master, height=30, bg=PANEL2):
        super().__init__(master, bg=bg, highlightthickness=0, width=1,
                         height=px(height))
        self.cells = []
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, cells):
        self.cells = cells
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or not self.cells:
            return
        cw = w / len(self.cells)
        for i, (label, val, col) in enumerate(self.cells):
            x0 = i * cw
            if i:
                self.create_line(x0, 2, x0, h - 2, fill=PANEL)
            cx = x0 + cw / 2
            self.create_text(cx, h * 0.33, text=label, fill=DIM, font=F_XS)
            self.create_text(cx, h * 0.72, text=val, fill=col, font=F_XS_B)


class ProgressBar(tk.Canvas):
    """Thin gold progress bar with a moving sheen while busy."""

    def __init__(self, master, height=6, bg=PANEL2):
        super().__init__(master, bg=bg, highlightthickness=0, width=1,
                         height=px(height))
        self.frac = 0.0
        self._phase = 0
        self._job = None
        self.bind("<Configure>", lambda e: self.redraw())
        # stop the sheen timer when the bar goes away with its screen
        self.bind("<Destroy>", lambda e: e.widget is self and self.stop(),
                  add="+")

    def set(self, frac: float):
        self.frac = max(0.0, min(1.0, frac))
        self.redraw()

    def start(self):
        if self._job is None:
            self._tick()

    def stop(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = None

    def _tick(self):
        self._phase += px(6)
        self.redraw()
        self._job = self.after(30, self._tick)

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        x = int(w * self.frac)
        self.create_rectangle(0, 0, x, h, fill=EDGE_HI, width=0)
        if self._job is not None and x > 0:
            band = self._phase % (x + px(120)) - px(60)
            self.create_rectangle(max(band, 0), 0, min(band + px(60), x), h,
                                  fill=ACCENT_HI, width=0)


# ------------------------------------------------------------------ table

@dataclass
class Col:
    key: str
    title: str
    width: int                        # in "0" characters of F_SM
    align: str = "center"             # w | center | e
    kind: str = "text"                # text | icon | bars | image | score
    sort: Callable[[dict], Any] | None = None
    tip: str | None = None
    stretch: bool = False
    raw_title: bool = False           # a player name: keep its own case


@dataclass
class Row:
    cells: dict[str, Any]             # key -> (text, colour) | bars | image
    icon: Any = None                  # icon key for the "icon" column
    marked: bool = False              # ◆ + gold text in the first text col
    data: Any = None
    extra: dict = field(default_factory=dict)
    tip: str | None = None            # hover text for the whole row


KEEP_CAPS = {"WR", "KDA", "CS", "GD", "XP", "VS", "W", "L", "K", "D", "A",
             "CI", "LP"}


def header_text(t: str) -> str:
    """DRAFT's table headers are Title case ("Champion", "WR lane"):
    'CS/MIN' -> 'CS/min', 'GD@15 ±' -> 'GD@15 ±', 'DIES <14:00' -> 'Dies
    <14:00', 'YOUR SCORE ±' -> 'Your score ±'."""
    out = []
    for i, word in enumerate(t.split(" ")):
        parts = []
        for j, part in enumerate(word.split("/")):
            head = "".join(ch for ch in part if ch.isalpha())
            if not head or head in KEEP_CAPS:
                parts.append(part)
            elif i == 0 and j == 0:
                parts.append(part[:1] + part[1:].lower())
            else:
                parts.append(part.lower())
        out.append("/".join(parts))
    return " ".join(out)


class Table(tk.Frame):
    """DRAFT's suggestion table, drawn on a canvas (hundreds of rows stay
    instant): dim bold headers, gold active sort column with a ▼/▲ arrow,
    gold divider, zebra rows, gold left bar + HOVER fill on hover, ◆ for
    highlighted rows, and an optional permanently highlighted top row."""

    def __init__(self, master, cols: list[Col], icon_loader=None,
                 on_click=None, rows_visible: int | None = 12,
                 top_highlight=False, tooltip: Tooltip | None = None,
                 default_sort: str | None = None, bg=PANEL):
        super().__init__(master, bg=bg)
        self.cols, self.icon_loader, self.on_click = cols, icon_loader, on_click
        self.top_highlight, self.tooltip, self.bg = top_highlight, tooltip, bg
        self.chw = char_w(F_SM)
        self.gap = max(int(0.45 * self.chw), 5)
        self.row_h = px(26)
        self.icon_px = px(22)
        self.rows: list[Row] = []
        self.view: list[Row] = []
        self.sort_key, self.sort_desc = default_sort, True
        self.hover = -1
        self.hdr = tk.Canvas(self, bg=bg, highlightthickness=0,
                             height=px(20))
        self.hdr.pack(fill="x")
        tk.Frame(self, bg=EDGE, height=1).pack(fill="x", pady=(SP_XS, 0))
        wrap = tk.Frame(self, bg=bg)
        wrap.pack(fill="both", expand=True, pady=(SP_S, 0))
        h = self.row_h * rows_visible if rows_visible else px(200)
        self.body = tk.Canvas(wrap, bg=bg, highlightthickness=0, height=h,
                              yscrollincrement=1)
        self.body.pack(side="left", fill="both", expand=True)
        self.body._scrollable = True
        if rows_visible:
            sb = SlimScroll(wrap, self.body)
            sb.configure(height=1)        # follow the body, never stretch it
            sb.pack(side="left", fill="y", padx=(px(4), 0))
        # draw rows lazily as they scroll into view (Matches: 300 rows x 11
        # columns took ~2 s to draw up front)
        thumb = self.body.cget("yscrollcommand")

        def on_scroll(first, last, _t=thumb):
            if _t:
                self.body.tk.call(_t, first, last)
            self._ensure_rows()
        self.body.configure(yscrollcommand=on_scroll)
        self._drawn_rows: set[int] = set()
        self._layout = None
        _TABLES.add(self)
        self._redraw_job = None
        self._drawn_w = None
        self.body.bind("<Configure>", self._on_body_configure)
        self.hdr.bind("<Configure>", lambda e: self.draw_header())
        self.body.bind("<Motion>", self._motion)
        self.body.bind("<Leave>", lambda e: (
            self._set_hover(-1), self.tooltip and getattr(
                self, "_tip_on", False) and self.tooltip.hide()))
        self.body.bind("<Button-1>", self._click)
        self._imgs: list = []

    # layout
    def _xs(self, width: int):
        xs, x = [], px(3)
        fixed = sum(c.width * self.chw + 2 * self.gap for c in self.cols
                    if c.kind != "icon") + sum(
            self.icon_px + px(8) for c in self.cols if c.kind == "icon")
        # spare < 0 in a narrow window: the stretch column gives up width
        # (down to 4 characters) instead of pushing the others off the edge
        spare = width - fixed - px(6)
        for c in self.cols:
            if c.kind == "icon":
                w = self.icon_px + px(8)
            else:
                w = c.width * self.chw + 2 * self.gap
                if c.stretch:
                    w = max(w + spare, 4 * self.chw + 2 * self.gap)
            xs.append((x, x + w))
            x += w
        return xs

    def _on_body_configure(self, e):
        if e.width != self._drawn_w:      # re-shown at the same width: skip
            self.redraw()

    def queue_redraw(self):
        """Coalesce bursts (e.g. 20 champion icons arriving) into one
        redraw ~40 ms later."""
        if self._redraw_job is None:
            self._redraw_job = self.after(40, self._run_queued)

    def _run_queued(self):
        self._redraw_job = None
        if self.winfo_exists():
            self.redraw()

    def set_rows(self, rows: list[Row], keep_sort=True) -> None:
        self.rows = rows
        self._apply_sort()
        self.body.yview_moveto(0)
        self.redraw()

    def _apply_sort(self):
        col = next((c for c in self.cols if c.key == self.sort_key), None)
        if col and col.sort:
            def k(r):
                v = col.sort(r)
                return (v is None or v != v, v if v is not None else 0)
            self.view = sorted(self.rows, key=k, reverse=False)
            if self.sort_desc:
                good = [r for r in self.view if not k(r)[0]]
                bad = [r for r in self.view if k(r)[0]]
                self.view = good[::-1] + bad
        else:
            self.view = list(self.rows)

    def draw_header(self):
        c = self.hdr
        c.delete("all")
        w = c.winfo_width()
        if w <= 1:
            return
        for col, (x0, x1) in zip(self.cols, self._xs(w)):
            if not col.title:
                continue
            active = col.key == self.sort_key
            anchor, x = self._anchor(col, x0, x1)
            t = c.create_text(x, px(10), text=col.title if col.raw_title
                              else header_text(col.title),
                              anchor=anchor,
                              fill=EDGE_HI if active else DIM, font=F_XS_B)
            if active:
                bb = c.bbox(t)
                ax, ay, s = bb[2] + px(5), px(10), px(3)
                pts = ((ax - s, ay - s / 2, ax + s, ay - s / 2, ax, ay + s)
                       if self.sort_desc else
                       (ax - s, ay + s / 2, ax + s, ay + s / 2, ax, ay - s))
                c.create_polygon(*pts, fill=EDGE_HI, outline="")
            if col.sort:
                c.tag_bind(t, "<Button-1>", lambda e, k=col.key: self.sort_by(k))
                c.tag_bind(t, "<Enter>", lambda e: c.configure(cursor="hand2"))
                c.tag_bind(t, "<Leave>", lambda e: (
                    c.configure(cursor=""),
                    self.tooltip and self.tooltip.hide()))
            if col.tip and self.tooltip:
                c.tag_bind(t, "<Enter>", lambda e, tip=col.tip:
                           self.tooltip.show(e.x_root, e.y_root, tip), add="+")

    def sort_by(self, key):
        if key == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key, self.sort_desc = key, True
        self._apply_sort()
        self.redraw()

    def _anchor(self, col, x0, x1):
        if col.align == "w":
            return "w", x0 + (0 if col.kind == "icon" else px(2))
        if col.align == "e":
            return "e", x1 - self.gap
        return "center", (x0 + x1) / 2

    def redraw(self):
        self.draw_header()
        c = self.body
        c.delete("all")
        self._imgs = []
        w = c.winfo_width()
        if w <= 1:
            return
        self._drawn_w = w
        self._layout = (w, self._xs(w))
        self._drawn_rows = set()
        c.configure(scrollregion=(0, 0, w, len(self.view) * self.row_h))
        self.hover = -1
        self._ensure_rows()

    def _ensure_rows(self, margin: int = 15):
        """Draw the rows in (or near) view that aren't drawn yet."""
        if self._layout is None or not self.view:
            return
        c, rh = self.body, self.row_h
        h = max(c.winfo_height(), rh)
        # the part of the canvas on screen: its own scroll position, cut
        # to the window (a full-height table inside a scrolling page)
        root = c.winfo_toplevel()
        by, ry = c.winfo_rooty(), root.winfo_rooty()
        v0 = max(0, ry - by)
        v1 = min(h, ry + root.winfo_height() - by)
        if v1 <= v0:                      # off screen: just the first rows
            v0, v1 = 0, min(h, root.winfo_height())
        top = c.canvasy(0)
        i0 = max(int((top + v0) // rh) - margin, 0)
        i1 = min(int((top + v1) // rh) + margin + 1, len(self.view))
        for i in range(i0, i1):
            if i not in self._drawn_rows:
                self._drawn_rows.add(i)
                self._draw_row(i)

    def _draw_row(self, i):
        c, rh = self.body, self.row_h
        w, xs = self._layout
        r = self.view[i]
        y0 = i * rh
        top = self.top_highlight and i == 0
        bg = TOP_BG if top else (PANEL if i % 2 else PANEL2)
        c.create_rectangle(0, y0 + 1, w, y0 + rh - 1, fill=bg, width=0,
                           tags=("bg", "bg%d" % i))
        c.create_rectangle(0, y0 + 1, px(3), y0 + rh - 1,
                           fill=EDGE_HI if top else bg, width=0,
                           tags=("bar", "bar%d" % i))
        cy = y0 + rh / 2
        first_text = True
        for col, (x0, x1) in zip(self.cols, xs):
            v = r.cells.get(col.key)
            if col.kind == "icon":
                img = self.icon_loader(r.icon, self.icon_px,
                                       self.queue_redraw) if (
                    self.icon_loader and r.icon) else None
                if img is not None:
                    self._imgs.append(img)
                    c.create_image(x0 + px(4), cy, image=img, anchor="w")
                else:
                    c.create_rectangle(x0 + px(4), cy - self.icon_px / 2,
                                       x0 + px(4) + self.icon_px,
                                       cy + self.icon_px / 2, fill=HOVER,
                                       width=0)
                continue
            if v is None:
                continue
            if col.kind == "bars":       # [(fraction, colour)…]
                bw = x1 - x0 - 2 * self.gap
                bx, bh = x0 + self.gap, px(9)
                for frac, colr in v:
                    seg = bw * max(frac, 0)
                    if seg >= 0.5:
                        c.create_rectangle(bx, cy - bh / 2, bx + seg,
                                           cy + bh / 2, fill=colr, width=0)
                    bx += seg
                continue
            if col.kind == "image":
                self._imgs.append(v)
                c.create_image((x0 + x1) / 2, cy, image=v)
                continue
            text, colr = v if isinstance(v, tuple) else (str(v), FG)
            anchor, x = self._anchor(col, x0, x1)
            if first_text and col.align == "w" and r.marked:
                text, colr = "◆ " + text, EDGE_HI
            first_text = first_text and col.align != "w"
            text = self._fit_text(text, x1 - x0 - self.gap - px(2))
            c.create_text(x, cy, text=text, anchor=anchor, fill=colr,
                          font=F_SM)

    def _fit_text(self, text: str, avail: float) -> str:
        """``text``, cut with "…" if it is wider than ``avail`` px (a long
        note in a narrow window would run into the next column)."""
        if len(text) * self.chw * 1.7 <= avail:      # surely fits: no measure
            return text
        f = getattr(self, "_font", None) or tkfont.Font(font=F_SM)
        self._font = f
        cw = getattr(self, "_cw", None)
        if cw is None:
            cw = self._cw = _char_widths(f)
        # per-character widths, cached once: a Tk measure per cell cost
        # ~2 ms, and the Matches/Stats lab tables have thousands of cells
        est = sum(cw(ch) for ch in text)
        if est <= avail:
            return text
        if est - len(text) <= avail and f.measure(text) <= avail:
            return text                      # kerning made it fit
        ell = cw("…")
        out, acc = "", 0.0
        for ch in text:
            if acc + cw(ch) + ell > avail:
                break
            out += ch
            acc += cw(ch)
        out = out.rstrip()
        return out + "…" if out else ""

    def _row_at(self, y):
        i = int(self.body.canvasy(y) // self.row_h)
        return i if 0 <= i < len(self.view) else -1

    def _set_hover(self, i):
        if i == self.hover:
            return
        c = self.body
        for j, active in ((self.hover, False), (i, True)):
            if j < 0:
                continue
            top = self.top_highlight and j == 0
            base = TOP_BG if top else (PANEL if j % 2 else PANEL2)
            c.itemconfigure("bg%d" % j, fill=HOVER if active else base)
            c.itemconfigure("bar%d" % j,
                            fill=EDGE_HI if (active or top) else base)
        self.hover = i
        c.configure(cursor="hand2" if (i >= 0 and self.on_click) else "")

    def _motion(self, e):
        i = self._row_at(e.y)
        changed = i != self.hover
        self._set_hover(i)
        if self.tooltip is None:
            return
        tip = self.view[i].tip if i >= 0 else None
        if tip:
            if changed or not getattr(self, "_tip_on", False):
                self.tooltip.show(e.x_root, e.y_root, tip)
                self._tip_on = True
        elif getattr(self, "_tip_on", False):
            self.tooltip.hide()
            self._tip_on = False

    def _click(self, e):
        i = self._row_at(e.y)
        if i >= 0 and self.on_click:
            self.on_click(self.view[i])


_CW_CACHE: dict = {}


def _char_widths(font):
    """A cached per-character width lookup for one Tk font."""
    key = (str(font), font.actual("size"), font.actual("weight"))
    table = _CW_CACHE.setdefault(key, {})

    def width(ch):
        w = table.get(ch)
        if w is None:
            w = table[ch] = font.measure(ch)
        return w
    return width


def bar_cells(frac: float, color: str) -> list[tuple[float, str]]:
    return [(frac, color)]
