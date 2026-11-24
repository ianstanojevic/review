"""Champions: DRAFT's suggestion table for your own champion pool (icon,
games, WR ±, KDA, CS/min, GD@15, damage share, score, a radar thumbnail and
an inline win/loss bar), ◆ on your best picks, plus the champion scatter."""
from __future__ import annotations

import math
import tkinter as tk

from ...theme.radar import thumbnail
from ...theme.tokens import (BAD, DIM, EDGE_HI, F_SM, FG, GOOD, PANEL,
                             PANEL2, score_color, signed_color)
from ...theme.widgets import Col, Row, Table, px
from .. import catalogue as C
from .. import takeaways as T
from .base import (Screen, avg_strip, clear, cname, grid, mean_pm, panel,
                   pm_text, wr_text)

MIN_BEST = 5


def best_picks(res) -> set[str]:
    """◆ = 5+ games, score above 50 with its CI clear of 50, or a WR whose
    Wilson lower bound beats 50%."""
    out = set()
    for c in res.champions:
        if c.games < MIN_BEST:
            continue
        sc = c.score
        if (sc.mean == sc.mean and sc.half == sc.half
                and sc.mean - sc.half > 50) or c.rate.ci[0] > 0.5:
            out.add(c.name)
    return out


class ChampionsScreen(Screen):
    def build(self):
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)
        self._thumbs = []

    def update_result(self, res):
        clear(self.box)
        self._thumbs = []
        best = best_picks(res)
        p, _ = panel(self.box, "CHAMPION POOL", EDGE_HI,
                     right="%d champions · n=%d games · ◆ = best picks" % (
                         len(res.champions), res.n_games), expand=False)
        kda_avg = next((("%.2f" % peer) for k, _, _, peer, _ in
                        getattr(res, "key_stats", []) if k == "kda"), None)
        avg_strip(p, res, [("WR", None, "50%"), ("KDA", None, kda_avg)
                           if kda_avg else ("KDA", "kda", None),
                           ("CS/min", "cs_per_min", None),
                           ("GD@15", "gold_diff_15", None),
                           ("DMG", "dmg_share", None),
                           ("deaths", "deaths", None)])
        cols = [Col("rank", "#", 3, "e"),
                Col("icon", "", 0, kind="icon"),
                Col("name", "CHAMPION", 13, "w", stretch=True,
                    sort=lambda r: r.data.name),
                Col("n", "GAMES", 6, "e",
                    sort=lambda r: (r.data.games, -r.extra["i"])),
                Col("wr", "WR ±", 9, "e", sort=lambda r: r.data.rate.p,
                    tip="Win rate; ± is how far it could be off with this few games"),
                Col("kda", "KDA", 6, "e", sort=lambda r: r.data.kda),
                Col("cs", "CS/MIN", 7, "e",
                    sort=lambda r: r.data.cs_per_min.mean),
                Col("gd", "GD@15 ±", 12, "e",
                    sort=lambda r: r.data.gd15.mean),
                Col("dmg", "DMG ±", 8, "e",
                    sort=lambda r: r.data.dmg_share.mean,
                    tip="Share of your team's damage to champions"),
                Col("sc", "SCORE ±", 9, "e",
                    sort=lambda r: r.data.score.mean,
                    tip="Mean game score (50 = typical same-role player)"),
                Col("radar", "RADAR", 4, kind="image"),
                Col("bar", "W / L", 9, kind="bars")]
        t = Table(p, cols, icon_loader=self.app.icons.get, rows_visible=14,
                  tooltip=self.tip, default_sort="n",
                  on_click=lambda r: self.app.set_champion(r.data.name))
        rows = []
        size = px(24)
        for i, c in enumerate(res.champions):
            rr = res.champion_radars.get(c.name)
            img = thumbnail(rr[1], size) if rr else None
            if img:
                self._thumbs.append(img)
            sc, gd, dm = c.score, c.gd15, c.dmg_share
            cells = {
                "rank": (str(i + 1), DIM), "name": cname(c.name),
                "n": str(c.games),
                "wr": ("%s %s" % (wr_text(c.rate), pm_text(c.rate)),
                       GOOD if c.rate.p > 0.5 else BAD if c.rate.p < 0.5
                       else FG),
                "kda": "%.2f" % c.kda,
                "cs": "%.1f" % c.cs_per_min.mean
                if c.cs_per_min.mean == c.cs_per_min.mean else "—",
                "gd": (mean_pm("gold_diff_15", gd), signed_color(gd.mean)),
                "dmg": ("%.0f%% ±%.0f" % (100 * dm.mean, 100 * dm.half)
                        if dm.half == dm.half else
                        ("%.0f%%" % (100 * dm.mean) if dm.mean == dm.mean
                         else "—"), FG),
                "sc": (("%.0f ±%.0f" % (sc.mean, sc.half)) if sc.half ==
                       sc.half else "%.0f" % sc.mean, score_color(sc.mean)),
                "bar": [(c.rate.p, GOOD), (1 - c.rate.p, BAD)]}
            if img:
                cells["radar"] = img
            rows.append(Row(cells, icon=c.name, marked=c.name in best,
                            data=c, extra={"i": i}))
        t.set_rows(rows)
        t.pack(fill="x")

        left, right = grid(self.box, 2, pady=(px(8), 0))
        p, _ = panel(left, "COMFORT VS RESULTS", EDGE_HI, right="",
                     expand=False)
        C.champion_scatter(p, self, res, height=300, label_top=6)
        p, _ = panel(right, "DAMAGE PROFILE", EDGE_HI,
                     right="physical · magic · true", expand=False)
        self._dmg(p, res)

    def _dmg(self, p, res):
        from ...theme.widgets import DmgBar
        for c in res.champions[:10]:
            mix = c.extra.get("dmg_mix")
            if not mix or not sum(mix):
                continue
            r = tk.Frame(p, bg=PANEL)
            r.pack(fill="x", pady=px(2))
            ic = self.app.icons.get(c.name, px(20), lambda: None)
            if ic is not None:
                self._thumbs.append(ic)
                tk.Label(r, image=ic, bg=PANEL).pack(side="left")
            tk.Label(r, text=cname(c.name), bg=PANEL, fg=FG, font=F_SM,
                     width=12, anchor="w").pack(side="left", padx=(px(6), 0))
            tk.Label(r, text="n=%d" % c.games, bg=PANEL, fg=DIM,
                     font=("Segoe UI", 8), width=6, anchor="e").pack(
                         side="right")
            bar = DmgBar(r, height=14, mini=True, bg=PANEL2)
            bar.pack(side="left", fill="x", expand=True, padx=(px(6), px(6)))
            bar.set(mix)
