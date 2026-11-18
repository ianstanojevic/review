"""Trends: pick a stat → 20-game rolling line with patch regions and a clear
verdict (slope 95% CI, role-adjusted), the verdict table for every tracked
stat, game score over time, performance by patch and rank snapshots."""
from __future__ import annotations

import tkinter as tk

from ...analysis import fmt_value
from ...theme import widgets as W
from ...theme.tokens import (BAD, DIM, EDGE_HI, F_LG_B, F_SM, FG, GHOST,
                             GOOD, PANEL)
from ...theme.widgets import Col, Row, Table, px
from .. import catalogue as C
from .. import takeaways as T
from .base import Screen, avg_word, clear, grid, mean_pm, panel

VERDICT_COL = {"improving": GOOD, "declining": BAD, "stable": FG,
               "rising": EDGE_HI, "falling": EDGE_HI, "insufficient": DIM}


class TrendsScreen(Screen):
    def build(self):
        self.key = None
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)

    def update_result(self, res):
        self.res = res
        keys = [t.key for t in res.trends]
        if self.key not in keys:
            self.key = keys[0] if keys else None
        self._render()

    def _pick(self, key):
        self.key = key
        self._render()

    def _render(self):
        res = self.res
        clear(self.box)
        b = self.box
        p, _ = panel(b, "ROLLING TREND", EDGE_HI, right="role-adjusted "
                     "verdict · slope 95% CI", expand=False)
        tabs = W.TabRow(p, "STAT", [(t.key, t.label) for t in res.trends],
                        self.key, on_change=self._pick, style="teal",
                        bg=PANEL, small=True)
        tabs.pack(anchor="w", pady=(0, px(8)))
        t = next((t for t in res.trends if t.key == self.key), None)
        if t is None:
            return
        tk.Label(p, text=T.trend(t), bg=PANEL, fg=VERDICT_COL[t.verdict],
                 font=F_LG_B, anchor="w", justify="left",
                 wraplength=px(1000)).pack(fill="x")
        pp = [(pt, m) for pt, m in t.per_patch if m.n >= 5]
        take = ("Per patch (raw, 5+ games): " + " · ".join(
            "%s %s (n=%d)" % (pt, mean_pm(t.key, m), m.n) for pt, m in pp)
            ) if pp else "Per-patch means need 5+ games on a patch."
        C.trend_chart(p, self, res, t, height=230, take=take)

        left, right = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(left, "EVERY TRACKED STAT", EDGE_HI,
                     right="first third → last third", expand=False)
        cols = [Col("stat", "STAT", 18, "w", stretch=True),
                Col("v", "VERDICT", 10, "w",
                    sort=lambda r: r.data.verdict),
                Col("ch", "CHANGE", 8, "e"),
                Col("avg", avg_word(res).upper(), 10, "e"),
                Col("a", "FIRST ⅓ VS AVG", 14, "e"),
                Col("z", "LAST ⅓ VS AVG", 14, "e")]
        tab = Table(p, cols, rows_visible=len(res.trends), tooltip=self.tip,
                    on_click=lambda r: self._pick(r.data.key))
        rows = []
        for tr in res.trends:
            ch = fmt_value(tr.key, tr.change)
            if not ch.startswith(("+", "-", "—")):
                ch = "+" + ch
            rows.append(Row({
                "stat": tr.label,
                "v": (tr.verdict, VERDICT_COL[tr.verdict]),
                "ch": (ch if tr.verdict != "insufficient" else "—",
                       VERDICT_COL[tr.verdict]),
                "avg": (fmt_value(tr.key, res.peer_means[tr.key])
                        if tr.key in getattr(res, "peer_means", {}) else
                        "—", EDGE_HI),
                "a": (mean_pm(tr.key, tr.first_third), DIM),
                "z": (mean_pm(tr.key, tr.last_third), FG)},
                marked=tr.key == self.key, data=tr))
        tab.set_rows(rows)
        tab.pack(fill="x")
        p, _ = panel(right, "OVERALL PERFORMANCE", EDGE_HI, right="",
                     expand=False)
        C.score_trend(p, self, res, height=170)

        left, right = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(left, "BY PATCH", EDGE_HI, right="", expand=False)
        C.patch_scores(p, self, res)
        p, _ = panel(right, "RANK", EDGE_HI, right="", expand=False)
        C.lp_history(p, self, res)
