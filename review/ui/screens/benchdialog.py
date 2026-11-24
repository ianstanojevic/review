"""Benchmark dialog: explain what a benchmark is, how many games will be
fetched and roughly how long it takes, then start the background build.
Opened from the VS tab row (when the benchmark doesn't exist yet) and from
Settings (to add more games)."""
from __future__ import annotations

import tkinter as tk

from ...benchmark import DEFAULT_GAMES, LEVELS, MIN_USABLE, estimate_minutes
from ...theme import anim
from ...theme import widgets as W
from ...theme.tokens import (DARK_BG, DIM, EDGE_HI, F_SM, F_XS, FG, PANEL,
                             SP_L, SP_M, SP_S)
from ...theme.widgets import px

SIZES = [(150, "150"), (300, "300"), (500, "500")]


class BenchmarkDialog(tk.Toplevel):
    def __init__(self, app, level: str):
        super().__init__(app.root)
        self.app, self.level = app, level
        lv = LEVELS[level]
        region = app.player["region"] if app.player else \
            app.prefs.get("region", "EUW")
        self.region = region
        have, _ = app.cache.benchmark_info(region, level)
        self.have = have
        self.title("%s benchmark" % lv.label)
        self.configure(bg=DARK_BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.bind("<Escape>", lambda e: self.destroy())
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        W.enable_dark_titlebar(self)
        p = W.CutPanel(self, padx=14)
        p.pack(padx=px(12), pady=px(12))
        inner = p.inner
        W.section_title(inner, "%s BENCHMARK · %s" % (lv.label.upper(),
                                                      region), EDGE_HI)
        txt = ("Score every account against the same %s, instead of the "
               "players in each account's own games. Then a 54 means the "
               "same thing on every account: how you compare with %s "
               "players in your role." % (lv.blurb, lv.label))
        tk.Label(inner, text=txt, bg=PANEL, fg=FG, font=F_SM,
                 justify="left", wraplength=px(440)).pack(anchor="w")
        tk.Label(inner, text="REVIEW samples random %s on %s and fetches "
                 "their recent ranked Solo/Duo games (all ten players in "
                 "each game count, so 300 games ≈ 600 players per role). "
                 "Games are cached, so this only happens once."
                 % (lv.blurb, region), bg=PANEL, fg=DIM, font=F_XS,
                 justify="left", wraplength=px(440)).pack(
                     anchor="w", pady=(SP_S, 0))
        if have:
            tk.Label(inner, text="Already cached: %d games." % have,
                     bg=PANEL, fg=DIM, font=F_XS).pack(anchor="w",
                                                       pady=(SP_S, 0))
        W.caption(inner, "GAMES TO FETCH" if not have else "ADD GAMES")
        self.size = W.TabRow(inner, None, SIZES, DEFAULT_GAMES if not have
                             else 150, style="teal", bg=PANEL,
                             on_change=lambda v: self._eta())
        self.size.pack(anchor="w")
        self.eta = tk.Label(inner, text="", bg=PANEL, fg=DIM, font=F_XS)
        self.eta.pack(anchor="w", pady=(SP_S, 0))
        self._eta()
        row = tk.Frame(inner, bg=PANEL)
        row.pack(anchor="w", pady=(SP_L, 0))
        W.button(row, "Build" if not have else "Fetch more", self._go,
                 gold=True).pack(side="left")
        W.button(row, "Cancel", self.destroy, fg=DIM).pack(
            side="left", padx=(SP_S, 0))
        tk.Label(inner, text="Runs in the background — keep using REVIEW. "
                 "Uses your API key's rate limit (shared with other "
                 "fetches).", bg=PANEL, fg=DIM, font=F_XS, justify="left",
                 wraplength=px(440)).pack(anchor="w", pady=(SP_M, 0))
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width()
                                      - self.winfo_reqwidth()) // 2
        y = app.root.winfo_rooty() + px(120)
        self.geometry("+%d+%d" % (x, y))
        anim.fade_in(self)

    def _eta(self):
        n = self.size.value
        m = estimate_minutes(n)
        self.eta.configure(text="≈ %d min with a personal API key "
                           "(~%d requests)" % (max(1, round(m)), n * 2 + 10))

    def _go(self):
        n = self.size.value
        self.app.build_benchmark(self.level, self.region, self.have + n)
        self.destroy()


__all__ = ["BenchmarkDialog", "MIN_USABLE"]
