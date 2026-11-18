"""Run the REVIEW desktop app.

    python -m review.ui
    python -m review.ui --snapshot OUTDIR --player "name#tag" --region EUW
        [--views overview,lane] [--role BOTTOM] [--champion Aphelios]

Snapshot mode opens a CACHED player off-screen (no API calls), renders each
view and saves window captures (PrintWindow: only REVIEW's own window).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="review.ui")
    ap.add_argument("--snapshot")
    ap.add_argument("--player")
    ap.add_argument("--region", default="EUW")
    ap.add_argument("--views", default="")
    ap.add_argument("--role")
    ap.add_argument("--champion")
    ap.add_argument("--vs", default=None, help="benchmark: diamond | master")
    ap.add_argument("--size", default="1240x900")
    ap.add_argument("--search", help="start fetching this Riot ID at launch")
    args = ap.parse_args(argv)

    from .app import VIEWS, App
    app = App()
    if not args.snapshot:
        if args.search:
            app.root.after(300, lambda: app.fetch(args.search, args.region))
        app.run()
        # don't wait for download threads still finishing an HTTP request
        # (up to 20 s): everything worth keeping is already committed to
        # SQLite, so leave immediately — the window is already gone
        import os
        os._exit(0)

    from ..riot_client import parse_riot_id
    from ..theme.widgets import px
    from .snapshot import capture
    out = Path(args.snapshot)
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(v) for v in args.size.split("x"))
    app.root.geometry("%dx%d+-6000+20" % (px(w), px(h)))
    name, tag = parse_riot_id(args.player)
    puuid = app.cache.find_player(name, tag, args.region)
    if not puuid:
        print("player not cached; run `python -m review fetch` first")
        return 1
    app.player = {"puuid": puuid, "riot_id": args.player,
                  "region": args.region}
    app.role, app.champion = args.role, args.champion
    app.reference = args.vs
    app.vs_row.select(args.vs, fire=False)
    app.role_row.select(args.role, fire=False)
    views = [v for v in args.views.split(",") if v] or [k for k, _ in VIEWS]

    def wait_result(tries=0):
        if app.result is None and tries < 300:
            app.root.after(100, lambda: wait_result(tries + 1))
            return
        shot(0)

    def shot(i):
        if i >= len(views):
            app.root.destroy()
            return
        spec = views[i]
        name, _, extra = spec.partition(":")
        app.navigate(name)
        s = app.screens.get(name)
        if extra and s is not None and hasattr(s, "snapshot_action"):
            s.snapshot_action(extra)

        def grab():
            path = out / ("%02d_%s.png" % (i, spec.replace(":", "_")))
            capture(app.root).save(path)
            print("saved", path)
            shot(i + 1)
        app.root.after(1800, grab)

    app._show_bars()
    app.reanalyze()
    app.root.after(200, wait_result)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
