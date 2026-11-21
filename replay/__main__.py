"""Run the REPLAY desktop app.

    python -m replay
    python -m replay --demo [--snapshot OUT.png] [--view review|insights|matchups]

``--demo`` opens a throwaway data folder with one bundled test game (no
API calls, nothing written to your real notes) — for trying the UI and
for the snapshot used in tests.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile


def _demo_data() -> dict:
    """A temp REVIEW_DATA_DIR holding the test game and a few notes."""
    import gzip
    import json
    from pathlib import Path
    d = tempfile.mkdtemp(prefix="replay-demo-")
    os.environ["REVIEW_DATA_DIR"] = d
    from review import config
    from review.cache import Cache
    from .events import build_game
    from .model import MISSED
    from .notes import Matchup, Notes
    fx = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    data = json.load(gzip.open(fx / "bot.json.gz"))
    c = Cache(config.db_path())
    c.put_match(data["match"])
    c.put_timeline(data["match"]["metadata"]["matchId"], data["timeline"])
    c.upsert_player("puuid-me", "Me", "TST", "EUW")
    c.close()
    g = build_game(data["match"], data["timeline"], "puuid-me")
    n = Notes(config.data_dir() / "replay.sqlite")
    n.ensure_deaths(g.match_id, g.puuid, [m.t_ms for m in g.deaths_list],
                    g.me.champion)
    ms = n.marks(g.match_id, g.puuid)
    cats = ["gank", "position", "vision", "overextend", "gank", "trade"]
    for mk, cat in zip(ms, cats):
        n.update_mark(mk.id, category=cat, avoidable=int(cat != "position"),
                      note="Pushed without vision; should have waited for "
                      "the wave" if cat == "gank" else "")
    n.add_mark(g.match_id, g.puuid, MISSED, 540_000, g.me.champion,
               "wave", "Recalled with a big wave at tower")
    n.save_matchup(Matchup(g.match_id, g.puuid, g.me.champion,
                           g.opponent.champion if g.opponent else None,
                           g.ally_support.champion if g.ally_support
                           else None,
                           g.enemy_support.champion if g.enemy_support
                           else None, 2,
                           "Can't fight lvl 2 — give the first wave, "
                           "punish after her E is down.", False))
    n.close()
    return {"puuid": "puuid-me", "riot_id": "Me#TST", "region": "EUW"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="replay")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--snapshot")
    ap.add_argument("--view", default="review")
    ap.add_argument("--size", default="1320x900")
    args = ap.parse_args(argv)

    player = _demo_data() if args.demo else None
    from .app import App
    app = App(demo=args.demo)
    if player:
        app.set_player(player)
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
        if args.view != "review":
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
