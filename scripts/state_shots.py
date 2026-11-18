"""M4 check: capture REVIEW's search, loading, empty and error states
(PrintWindow of REVIEW's own off-screen window; no API calls).

    python scripts/state_shots.py OUTDIR
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from review.riot_client import (InvalidKey, MissingKey,  # noqa: E402
                                PlayerNotFound, RateLimited,
                                ServiceUnavailable)
from review.sync import Progress  # noqa: E402
from review.theme.widgets import px  # noqa: E402
from review.ui.app import App  # noqa: E402
from review.ui.snapshot import capture  # noqa: E402


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    app = App()
    app.root.geometry("%dx%d+-6000+20" % (px(1240), px(900)))
    steps = [
        ("search", lambda: app.show_search()),
        ("loading", lambda: (app._show_loader("Somebody#EUW", "EUW"),
                             app.loader.progress(Progress(
                                 "games", 47, 150, "Fetching 47/150 games…",
                                 eta_s=251)))),
        ("err_notfound", lambda: app.show_error(PlayerNotFound(
            "No account called Somebody#EUW."), retry=lambda: None)),
        ("err_key", lambda: app.show_error(InvalidKey(403))),
        ("err_missing", lambda: app.show_error(MissingKey())),
        ("err_rate", lambda: app.show_error(RateLimited(),
                                            retry=lambda: None)),
        ("err_down", lambda: app.show_error(ServiceUnavailable("HTTP 503"),
                                            retry=lambda: None)),
        ("err_generic", lambda: app.show_error(KeyError("participants"))),
    ]

    def run(i=0):
        if i >= len(steps):
            app.root.destroy()
            return
        name, fn = steps[i]
        fn()

        def grab():
            capture(app.root).save(out / ("%02d_%s.png" % (i, name)))
            print("saved", name)
            run(i + 1)
        app.root.after(900, grab)
    app.root.after(600, run)
    app.root.mainloop()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
