"""DRAFT's colour-lerp animation engine, as free functions.

Smooth transitions instead of hard config swaps: hover glows, fade-ins,
count-ups. Every animation is cancel-safe (a new tween on the same option
cancels the running one).
"""
from __future__ import annotations

import tkinter as tk

from .tokens import mix

FRAME_MS = 16


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def stop_wcol(w, *opts) -> None:
    for opt in opts:
        job = getattr(w, "_ca_" + opt, None)
        if job:
            try:
                # cancel through the SAME widget that scheduled it: tkinter
                # tracks the callback in w._tclCommands, and cancelling via
                # another widget (e.g. the root) deletes the Tcl command but
                # leaves w's record, so w.destroy() later raised "can't
                # delete Tcl command" — which aborted screen switches and
                # even the X button
                w.after_cancel(job)
            except Exception:
                pass
            setattr(w, "_ca_" + opt, None)


def animate_wcol(w, opt: str, to: str, n: int = 7, delay: int = 0) -> None:
    """Tween a widget colour option (bg / fg / highlightbackground)."""
    stop_wcol(w, opt)
    try:
        frm = w.cget(opt)
    except tk.TclError:
        return
    if not (isinstance(frm, str) and frm.startswith("#") and len(frm) == 7):
        try:
            w.configure(**{opt: to})
        except tk.TclError:
            pass
        return

    def step(k=0):
        if not w.winfo_exists():
            return
        try:
            w.configure(**{opt: mix(frm, to, ease_out(k / n))})
        except tk.TclError:
            return
        if k < n:
            setattr(w, "_ca_" + opt, w.after(FRAME_MS, lambda: step(k + 1)))
    if delay:
        setattr(w, "_ca_" + opt, w.after(delay, step))
    else:
        step()


def style(w, bg: str | None = None, fg: str | None = None) -> None:
    """Set bg/fg AND the hover base colours, so hoverfx returns here."""
    if bg is not None:
        stop_wcol(w, "bg")
        w.configure(bg=bg)
        w._base_bg = bg
    if fg is not None:
        stop_wcol(w, "fg")
        w.configure(fg=fg)
        w._base_fg = fg


def hoverfx(w, bg_to: str | None = None, fg_to: str | None = None) -> None:
    """Hover glow: tween toward lighter colours on Enter, back on Leave."""
    w._base_bg = w.cget("bg")
    try:
        w._base_fg = w.cget("fg")
    except tk.TclError:
        w._base_fg = None

    def enter(_):
        if getattr(w, "_no_hover", False):
            return
        if bg_to:
            animate_wcol(w, "bg", bg_to)
        if fg_to:
            animate_wcol(w, "fg", fg_to)

    def leave(_):
        if getattr(w, "_no_hover", False):
            return
        animate_wcol(w, "bg", w._base_bg)
        if fg_to and w._base_fg:
            animate_wcol(w, "fg", w._base_fg)
    w.bind("<Enter>", enter, add="+")
    w.bind("<Leave>", leave, add="+")


def fade_in(win, val: float = 0.0) -> None:
    if not win.winfo_exists():
        return
    try:
        win.attributes("-alpha", val)
    except tk.TclError:
        return
    if val < 1.0:
        win.after(FRAME_MS, lambda: fade_in(win, min(1.0, val + 0.13)))


def count_up(label, target: float, fmt: str, frm: float = 50.0,
             n: int = 14, delay: int = 0) -> None:
    """Tick a label's number from frm to target (cubic ease-out)."""
    def step(k=0):
        if not label.winfo_exists():
            return
        if k >= n:
            label.configure(text=fmt % target)
            return
        label.configure(text=fmt % (frm + (target - frm) * ease_out(k / n)))
        label.after(FRAME_MS, lambda: step(k + 1))
    if delay:
        label.after(delay, step)
    else:
        step()
