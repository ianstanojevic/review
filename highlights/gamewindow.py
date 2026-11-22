"""League's game window (Windows only).

The replay recorder captures the game window, so it must be visible: in
full screen (``WindowMode=0``) the game collapses its window to 1×1 as soon
as another window has focus, and the recorder then silently records
nothing (checked live on 16.23: ``/replay/recording`` reads back 16×0 and
``recording`` stays false). Borderless/windowed keeps rendering.
"""
from __future__ import annotations

import sys
import time

TITLE = "League of Legends (TM) Client"


def find() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u.GetWindowTextW(h, buf, 64)
        if buf.value == TITLE:
            found.append(h)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


def size(h: int | None) -> tuple[int, int]:
    if not h or sys.platform != "win32":
        return (0, 0)
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(h, ctypes.byref(r))
    return r.right, r.bottom


def visible(h: int | None = None) -> bool:
    w, hh = size(h or find())
    return w > 100 and hh > 100


def bring_to_front(h: int | None = None, wait: float = 3.0) -> bool:
    """Put the game window in front (restores a collapsed full-screen
    game). Windows only allows this from the app that has focus, which is
    us when the user has just pressed Record; AttachThreadInput covers the
    rest. Returns True once the window has a real size."""
    h = h or find()
    if not h:
        return False
    import ctypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    me = k.GetCurrentThreadId()
    other = u.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and u.AttachThreadInput(
        me, other, True))
    try:
        u.keybd_event(0x12, 0, 0, 0)          # ALT tap unlocks focus change
        u.keybd_event(0x12, 0, 2, 0)
        u.ShowWindow(h, 9)                    # SW_RESTORE
        u.BringWindowToTop(h)
        u.SetForegroundWindow(h)
    finally:
        if attached:
            u.AttachThreadInput(me, other, False)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if visible(h):
            return True
        time.sleep(0.1)
    return visible(h)
