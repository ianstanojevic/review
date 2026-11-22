"""Run the HIGHLIGHTS desktop app.

    python -m highlights
    python -m highlights --demo [--snapshot OUT.png] [--view editor|clips]

``--demo`` opens a throwaway data folder with two bundled test games (no
API calls, no League client); Record simulates the flow and writes
nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile


def _demo_data() -> dict:
    """A temp REVIEW_DATA_DIR holding the test games."""
    import gzip
    import json
    from pathlib import Path
    d = tempfile.mkdtemp(prefix="highlights-demo-")
    os.environ["REVIEW_DATA_DIR"] = d
    from review import config
    from review.cache import Cache
    fx = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    c = Cache(config.db_path())
    for name in ("support", "bot"):
        data = json.load(gzip.open(fx / ("%s.json.gz" % name)))
        c.put_match(data["match"])
        c.put_timeline(data["match"]["metadata"]["matchId"],
                       data["timeline"])
    c.upsert_player("puuid-me", "Me", "TST", "EUW")
    c.close()
    # clips land in the temp folder too, never in your Videos
    (Path(d) / "highlights_prefs.json").write_text(json.dumps(
        {"out_dir": str(Path(d) / "Highlights")}), encoding="utf-8")
    return {"puuid": "puuid-me", "riot_id": "Me#TST", "region": "EUW",
            "open": json.load(gzip.open(fx / "support.json.gz"))["match"]
            ["metadata"]["matchId"]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="highlights")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--snapshot")
    ap.add_argument("--view", default="editor")
    ap.add_argument("--size", default="1320x620")
    args = ap.parse_args(argv)

    player = _demo_data() if args.demo else None
    from .app import App
    app = App(demo=args.demo)
    if player:          # open the game with the most highlights
        first = player.pop("open")
        app.player = player
        app._banner()
        app.reload_games(then=lambda: app.open_game(first))
    if not args.snapshot:
        app.run()
        os._exit(0)

    from review.theme.widgets import px
    w, h = (int(v) for v in args.size.split("x"))
    windows = sys.platform == "win32"
    # Windows: off-screen + PrintWindow (only our own window, like REVIEW's
    # snapshot); elsewhere (Xvfb) a screen grab at 0,0.
    app.root.geometry("%dx%d+%d+20" % (px(w), px(h), -6000 if windows else 0))

    def shot():
        if args.view != "editor":
            app.navigate(args.view)
        app.root.after(1500, grab)

    def grab():
        app.root.update()
        if windows:
            from review.ui.snapshot import capture
            img = capture(app.root)
        else:
            from PIL import ImageGrab
            x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
            img = ImageGrab.grab(bbox=(x, y, x + app.root.winfo_width(),
                                       y + app.root.winfo_height()),
                                 xdisplay=os.environ.get("DISPLAY"))
        img.save(args.snapshot)
        print("saved", args.snapshot)
        app.root.destroy()
    app.root.after(2500, shot)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
