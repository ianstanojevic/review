"""Wins vs losses: which stats separate your wins from your losses, ranked
by effect size (Cohen's d, 90% CI) as a diverging chart, the same stats as
split bars (teal = mean in wins, red = mean in losses), and the four
win-rate curves."""
from __future__ import annotations

import math
import tkinter as tk

from ...analysis import METRICS, fmt_value
from ...theme.charts import DivergingBars, DivItem
from ...theme.tokens import BAD, DIM, EDGE_HI, F_SM, FG, GOOD, PANEL
from ...theme.widgets import Col, Row, Table, px
from .. import catalogue as C
from .. import takeaways as T
from .base import Screen, chart_block, clear, grid, panel


class WinLossScreen(Screen):
    def build(self):
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)

    def update_result(self, res):
        clear(self.box)
        b = self.box
        ds = res.deciders
        nw, nl = res.record.wins, res.record.n - res.record.wins
        p, _ = panel(b, "WHAT DECIDES YOUR GAMES", EDGE_HI,
                     right="%d wins · %d losses · Cohen's d, 90%% CI" % (
                         nw, nl), expand=False)
        items = []
        for d in ds:
            e = d.effect
            items.append(DivItem(
                d.label, e.d, e.lo, e.hi, "early" if d.early else "",
                "%s — wins %s vs losses %s (n=%d/%d), d = %+.2f [%+.2f, "
                "%+.2f]" % (d.label, fmt_value(d.key, e.mean_a),
                            fmt_value(d.key, e.mean_b), e.n_a, e.n_b, e.d,
                            e.lo, e.hi)))
        ch = chart_block(p, DivergingBars, "EFFECT SIZE (WINS − LOSSES)",
                         height=max(160, 26 * len(items) + 40), tip=self.tip)
        ch.set(items=items, label_w=230,
               xlabel="effect size d: how much higher the stat is in wins "
               "than in losses")
        ch.meta("n=%d games" % res.n_games, T.deciders(res))

        p, _ = panel(b, "WINS VS LOSSES, SIDE BY SIDE", EDGE_HI,
                     right="mean per game", expand=False, pady=(px(8), 0))
        cols = [Col("stat", "STAT", 20, "w", stretch=True,
                    sort=lambda r: r.data.label),
                Col("w", "IN WINS", 9, "e"),
                Col("l", "IN LOSSES", 9, "e"),
                Col("bar", "", 16, kind="bars"),
                Col("d", "d ± (90%)", 12, "e",
                    sort=lambda r: abs(r.data.effect.d))]
        t = Table(p, cols, rows_visible=min(len(ds), 14) or 3,
                  tooltip=self.tip, default_sort="d")
        rows = []
        for d in ds:
            e = d.effect
            a, z = e.mean_a, e.mean_b
            bars = []
            if a == a and z == z:
                lo = min(a, z, 0)
                span = (max(abs(a), abs(z)) or 1)
                fa, fz = abs(a) / span / 2, abs(z) / span / 2
                bars = [(fa, GOOD), (0.5 - fa, PANEL), (fz, BAD)]
            pol = METRICS[d.key].polarity if d.key in METRICS else 1
            good = (e.d > 0) == (pol >= 0)
            rows.append(Row({
                "stat": d.label + ("  · early" if d.early else ""),
                "w": (fmt_value(d.key, a), GOOD),
                "l": (fmt_value(d.key, z), BAD),
                "bar": bars,
                "d": ("%+.2f ±%.2f" % (e.d, (e.hi - e.lo) / 2),
                      GOOD if good else BAD)}, data=d))
        t.set_rows(rows)
        t.pack(fill="x")

        g = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(g[0], "WIN RATE BY LANE STATE", EDGE_HI, right="",
                     expand=False)
        C.wr_bars(p, self, res, "gd15", "BY GOLD DIFF @15", T.wr_gd15)
        C.wr_bars(p, self, res, "cs10", "BY CS DIFF @10", T.wr_cs10)
        p, _ = panel(g[1], "WIN RATE BY GAME SHAPE", EDGE_HI, right="",
                     expand=False)
        C.wr_bars(p, self, res, "early_deaths", "BY DEATHS BEFORE 14:00",
                  T.wr_early_deaths)
        C.wr_bars(p, self, res, "length", "BY GAME LENGTH", T.wr_length)
