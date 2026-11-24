"""Trends: pick a stat → 20-game rolling line with patch regions and a clear
verdict (slope 95% CI, role-adjusted), the verdict table for every tracked
stat, game score over time, performance by patch and rank snapshots."""
from __future__ import annotations

import tkinter as tk

from ...analysis import fmt_value
from ...analysis.metrics import help_text
from ...theme import widgets as W
from ...theme.tokens import (BAD, DIM, EDGE_HI, F_LG_B, F_SM, FG, GHOST,
                             GOOD, PANEL)
from ...theme.widgets import Col, Row, Table, px
from .. import catalogue as C
from .. import takeaways as T
from .base import (Screen, avg_word, clear, fit_wrap, grid, mean_pm,
                   panel)

VERDICT_COL = {"improving": GOOD, "declining": BAD, "stable": FG,
               "rising": EDGE_HI, "falling": EDGE_HI, "insufficient": DIM}


VERDICT_WORD = {"improving": "↑ better", "declining": "↓ worse",
                "stable": "steady", "rising": "↑ up", "falling": "↓ down",
                "insufficient": "too few games"}


def trend_cell(tr):
    rec = getattr(tr, "recent", None)
    if tr.verdict == "stable" and rec:
        return ({"improving": "↑ lately", "declining": "↓ lately",
                 "rising": "↑ lately", "falling": "↓ lately"}[rec],
                VERDICT_COL[rec])
    return (VERDICT_WORD[tr.verdict], VERDICT_COL[tr.verdict])


def vs_avg(key, m):
    """'0.54 below' — your value vs the group average, teal when clearly
    better, red when clearly worse (90% range doesn't reach the average)."""
    from ...analysis import METRICS
    if not m.n or m.mean != m.mean:
        return ("—", DIM)
    mag = fmt_value(key, abs(m.mean)).lstrip("+")
    if fmt_value(key, 0.0).lstrip("+") == mag:
        return ("at avg", FG)
    text = "%s %s" % (mag, "above" if m.mean > 0 else "below")
    pol = METRICS[key].polarity
    sure = m.se == m.se and abs(m.mean) > 1.645 * m.se
    if not pol or not sure:
        return (text, FG)
    return (text, GOOD if (m.mean > 0) == (pol > 0) else BAD)


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
        p, _ = panel(b, "TREND", EDGE_HI, right="pick a stat",
                     expand=False)
        tabs = W.TabRow(p, "STAT", [(t.key, t.label) for t in res.trends],
                        self.key, on_change=self._pick, style="teal",
                        bg=PANEL, small=True)
        tabs.pack(anchor="w", pady=(0, px(8)))
        t = next((t for t in res.trends if t.key == self.key), None)
        if t is None:
            return
        head = fit_wrap(tk.Label(p, text=T.trend(t, res), bg=PANEL,
                 fg=VERDICT_COL[getattr(t, "recent", None) or t.verdict],
                 font=F_LG_B, anchor="w", justify="left",
                 wraplength=px(1000)))
        head.pack(fill="x")
        pp = [(pt, m) for pt, m in t.per_patch if m.n >= 5]
        take = ("Per patch (raw, 5+ games): " + " · ".join(
            "%s %s (n=%d)" % (pt, mean_pm(t.key, m), m.n) for pt, m in pp)
            ) if pp else "Per-patch means need 5+ games on a patch."
        C.trend_chart(p, self, res, t, height=230, take=take)

        left, right = grid(b, 2, pady=(px(8), 0))
        grp = getattr(res, "reference_label", None) or "peer"
        p, _ = panel(left, "EVERY STAT", EDGE_HI,
                     right="you vs %s average · click a row" % grp,
                     expand=False)
        third = max(res.n_games // 3, 1)
        cols = [Col("stat", "STAT", 18, "w", stretch=True),
                Col("v", "TREND", 10, "w",
                    sort=lambda r: r.data.verdict),
                Col("a", "OLDEST %d" % third, 11, "e",
                    tip="Your oldest %d games: how far above or below the "
                    "%s average you were." % (third, grp)),
                Col("z", "NEWEST %d" % third, 11, "e",
                    tip="Your newest %d games: how far above or below the "
                    "%s average you are." % (third, grp)),
                Col("avg", avg_word(res).upper(), 11, "e")]
        tab = Table(p, cols, rows_visible=len(res.trends), tooltip=self.tip,
                    on_click=lambda r: self._pick(r.data.key))
        rows = []
        for tr in res.trends:
            rows.append(Row({
                "stat": tr.label,
                "v": trend_cell(tr),
                "avg": (fmt_value(tr.key, res.peer_means[tr.key])
                        if tr.key in getattr(res, "peer_means", {}) else
                        "—", DIM),
                "a": vs_avg(tr.key, tr.first_third),
                "z": vs_avg(tr.key, tr.last_third)},
                marked=tr.key == self.key, data=tr,
                tip=help_text(tr.key)))
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
