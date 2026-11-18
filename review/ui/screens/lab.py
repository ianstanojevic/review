"""Stats lab: the full chart catalogue in tabs (Lane / Trends / Champions /
Sessions / Deaths / Items / Duos). Each tab is built on demand. Right-click
any chart to export it as a PNG (with its title and takeaway)."""
from __future__ import annotations

import tkinter as tk

from ...theme import widgets as W
from ...theme.tokens import DIM, EDGE_HI, F_XS, PANEL
from ...theme.widgets import px
from .. import catalogue as C
from .. import takeaways as T
from .base import Screen, clear, empty_note, grid, panel

TABS = [("lane", "Lane"), ("trends", "Trends"), ("champions", "Champions"),
        ("sessions", "Sessions"), ("deaths", "Deaths"), ("items", "Items"),
        ("duos", "Duos")]
DISTS = (("cs_per_min", "CS/min"), ("kp", "Kill participation"),
         ("dmg_share", "Damage share"), ("deaths", "Deaths"),
         ("vision_per_min", "Vision/min"), ("gold_per_min", "Gold/min"))


class LabScreen(Screen):
    def build(self):
        self.tab = "lane"
        head = tk.Frame(self.body, bg=self.body["bg"])
        head.pack(fill="x", pady=(0, px(8)))
        self.tabs = W.TabRow(head, "LAB", TABS, self.tab,
                             on_change=self._pick, style="teal", small=True)
        self.tabs.pack(side="left")
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)

    def update_result(self, res):
        self.res = res
        self._render()

    def snapshot_action(self, extra):
        self.tabs.select(extra)

    def _pick(self, tab):
        self.tab = tab
        self._render()
        self.area.to_top()

    def _render(self):
        clear(self.box)
        getattr(self, "_" + self.tab)(self.box, self.res)

    # ------------------------------------------------------------- tabs
    def _lane(self, b, res):
        p, _ = panel(b, "1 · LANE CURVES", EDGE_HI, right="wins vs losses, "
                     "90% CI", expand=False)
        for col, metric in zip(grid(p, 3, bg=PANEL), ("gold", "cs", "xp")):
            C.lane_curve(col, self, res, metric, height=180)
        p, _ = panel(b, "3 · DISTRIBUTIONS", EDGE_HI, right="per game, you "
                     "vs same-role peers in your games", expand=False,
                     pady=(px(8), 0))
        if not res.distributions:
            empty_note(p, "Needs games with a known role.")
        else:
            for i in range(0, len(DISTS), 3):
                for col, (k, lab) in zip(grid(p, 3, bg=PANEL),
                                         DISTS[i:i + 3]):
                    C.distribution(col, self, res, k, lab)
        g = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(g[0], "4 · WIN RATE CURVES", EDGE_HI, right="",
                     expand=False)
        C.wr_bars(p, self, res, "gd15", "BY GOLD DIFF @15", T.wr_gd15)
        C.wr_bars(p, self, res, "early_deaths", "BY DEATHS BEFORE 14:00",
                  T.wr_early_deaths)
        p, _ = panel(g[1], "4 · WIN RATE CURVES", EDGE_HI, right="",
                     expand=False)
        C.wr_bars(p, self, res, "length", "BY GAME LENGTH", T.wr_length)
        C.wr_bars(p, self, res, "cs10", "BY CS DIFF @10", T.wr_cs10)
        p, _ = panel(b, "6 · MATCHUPS", EDGE_HI, right="2+ games vs the "
                     "same laner", expand=False, pady=(px(8), 0))
        if res.matchups:
            C.matchup_table(p, self, res, rows_visible=10)
        else:
            empty_note(p, "No lane opponent repeats yet.")

    def _trends(self, b, res):
        p, _ = panel(b, "2 · ROLLING TRENDS", EDGE_HI, right="20-game "
                     "windows · patches shaded · role-adjusted verdicts",
                     expand=False)
        ts = res.trends
        for i in range(0, len(ts), 2):
            for col, t in zip(grid(p, 2, bg=PANEL), ts[i:i + 2]):
                C.trend_chart(col, self, res, t, height=170)
        g = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(g[0], "GAME SCORE", EDGE_HI, right="", expand=False)
        C.score_trend(p, self, res, height=170)
        C.patch_scores(p, self, res)
        p, _ = panel(g[1], "12 · RANK SNAPSHOTS", EDGE_HI, right="",
                     expand=False)
        C.lp_history(p, self, res, height=200)

    def _champions(self, b, res):
        p, _ = panel(b, "5 · CHAMPION SCATTER", EDGE_HI, right="",
                     expand=False)
        C.champion_scatter(p, self, res, height=360)

    def _sessions(self, b, res):
        g = grid(b, 2)
        p, _ = panel(g[0], "7 · SESSIONS", EDGE_HI, right="", expand=False)
        C.session_bars(p, self, res, "number")
        p, _ = panel(g[1], "7 · TILT", EDGE_HI, right="", expand=False)
        C.session_bars(p, self, res, "losses")
        p, _ = panel(b, "7 · WHEN YOU PLAY", EDGE_HI, right="local time",
                     expand=False, pady=(px(8), 0))
        C.heatmap(p, self, res, height=210)

    def _deaths(self, b, res):
        g = grid(b, 2)
        p, _ = panel(g[0], "8 · DEATH MAP", EDGE_HI, right="all phases · "
                     "see Deaths for the phase filter", expand=False)
        C.death_map(p, self, res, height=380)
        p, _ = panel(g[1], "8 · DEATH TIMING", EDGE_HI, right="",
                     expand=False)
        C.death_minutes(p, self, res, height=190)
        C.wr_bars(p, self, res, "early_deaths", "WIN RATE BY DEATHS BEFORE "
                  "14:00", T.wr_early_deaths)

    def _items(self, b, res):
        g = grid(b, 2)
        p, _ = panel(g[0], "9 · ITEM TIMING", EDGE_HI, right="",
                     expand=False)
        C.item_scatter(p, self, res, height=230)
        p, _ = panel(g[1], "9 · ITEM TIMING — BINNED", EDGE_HI, right="",
                     expand=False)
        C.item_bars(p, self, res, height=230)

    def _duos(self, b, res):
        p, _ = panel(b, "11 · DUO PARTNERS", EDGE_HI, right="teammates in "
                     "3+ of these games", expand=False)
        C.duo_table(p, self, res, rows_visible=12)
