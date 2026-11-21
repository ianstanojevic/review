"""Where a window opens: the saved size/position if it is visible, else a
default size centred above the taskbar. Never remembers an off-screen spot
(a window saved at x=-6000 once made REVIEW open invisibly).

Shared by REVIEW and REPLAY; kept free of UI imports so REPLAY doesn't load
REVIEW's whole UI (that import made a second Tk() in one test process fail
about half the time on Windows).
"""
from __future__ import annotations

import re

from .widgets import px


def parse_geo(g: str | None):
    """'WxH+X+Y' (X/Y may be negative, e.g. '+-6000') -> (w, h, x, y)."""
    m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", g or "")
    return tuple(int(v) for v in m.groups()) if m else None


def virtual_screen() -> tuple[int, int, int, int]:
    """(x, y, w, h) of all monitors together, in pixels."""
    try:
        import ctypes
        m = ctypes.windll.user32.GetSystemMetrics
        w, h = m(78), m(79)
        if w > 0 and h > 0:
            return m(76), m(77), w, h
    except Exception:
        pass
    return 0, 0, 1920, 1080


def on_screen(w, h, x, y) -> bool:
    """At least a good chunk of the title bar is on some monitor."""
    vx, vy, vw, vh = virtual_screen()
    return (x + w - px(150) > vx and x + px(150) < vx + vw
            and y >= vy - px(10) and y + px(40) < vy + vh)


def work_area(root) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the primary screen minus the taskbar."""
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0,
                                                      ctypes.byref(r), 0):
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 48


def safe_geometry(root, saved: str | None,
                  default: tuple[int, int] = (1240, 900)) -> str:
    """The saved size/position if it is visible; otherwise ``default``
    (logical px) centred in the work area (above the taskbar). A window
    bigger than the screen (e.g. saved on a larger monitor) is shrunk.
    Sizes are the client area: the title bar and frame come on top."""
    ax, ay, aw, ah = work_area(root)
    chrome_w, chrome_h = px(16), px(40)      # frame + title bar
    max_w, max_h = aw - chrome_w, ah - chrome_h
    g = parse_geo(saved) if saved else None
    if g:
        w, h, x, y = g
        w, h = min(w, max_w), min(h, max_h)
        if on_screen(w, h, x, y):
            return "%dx%d+%d+%d" % (w, h, x, y)
    w = min(px(default[0]), max_w - px(20))
    h = min(px(default[1]), max_h - px(10))
    x = ax + max((aw - w - chrome_w) // 2, 0)
    y = ay + max((ah - h - chrome_h) // 2, 0)
    return "%dx%d+%d+%d" % (w, h, x, y)


def remember(geometry: str) -> bool:
    """Is this ``root.geometry()`` worth saving (parsable and visible)?"""
    g = parse_geo(geometry)
    return bool(g) and on_screen(*g)
