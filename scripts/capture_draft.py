"""Capture DRAFT's own window (PrintWindow, off-screen) for the M4
side-by-side style check. Never grabs the screen and never saves DRAFT's
window state (the window is destroyed without DRAFT's close handler).

    python scripts/capture_draft.py PATH\\TO\\draft_tool_v2.py OUT.png
        [--size 1240x900] [--wait 8]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("out")
    ap.add_argument("--size", default="")
    ap.add_argument("--wait", type=float, default=8.0)
    a = ap.parse_args()
    import ctypes
    try:                     # render at the real DPI, like REVIEW does
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    spec = importlib.util.spec_from_file_location("draft_app", a.draft)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(a.draft).parent))
    spec.loader.exec_module(mod)
    app = mod.App()
    print("draft scale", getattr(app, "s", None), app.winfo_reqwidth(), app.winfo_reqheight())
    w, h = (app.winfo_width(), app.winfo_height())
    if a.size:
        w, h = (int(v) for v in a.size.split("x"))
    else:
        app.update_idletasks()
        w, h = app.winfo_reqwidth(), min(app.winfo_reqheight(), 1000)
    app.geometry("%dx%d+-6000+20" % (w, h))

    from review.ui.snapshot import capture

    def grab():
        try:
            app.attributes("-alpha", 1.0)
        except Exception:
            pass
        capture(app).save(a.out)
        print("saved", a.out)
        app.destroy()          # NOT app._on_close: don't touch DRAFT's state
    app.after(int(a.wait * 1000), grab)
    app.mainloop()


if __name__ == "__main__":
    main()
