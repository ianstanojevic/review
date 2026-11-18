"""Lane phase: @10/@15 diffs vs the direct lane opponent per role and per
champion, lane curves (wins vs losses), lane win-rate curves, the early-death
breakdown and the matchup table."""
from __future__ import annotations

import tkinter as tk

from ...analysis import ROLE_LABEL
from ...theme.tokens import BAD, DIM, EDGE_HI, F_SM, FG, GOOD, PANEL
from ...theme.widgets import Col, Row, Table, px
from .. import catalogue as C
from .. import takeaways as T
from .base import (Screen, avg_strip, clear, cname, empty_note, grid,
                   mean_pm, panel, rate_pm)
from ...theme.tokens import signed_color

DIFFS = ("cs_diff_10", "cs_diff_15", "gold_diff_10", "gold_diff_15",
         "xp_diff_15")


class LaneScreen(Screen):
    def build(self):
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)

    def update_result(self, res):
        clear(self.box)
        b = self.box
        # --- diffs table: roles first, then champions (3+ games)
        p, _ = panel(b, "LANE PHASE VS YOUR DIRECT OPPONENT", EDGE_HI,
                     right="mean ± 90% CI", expand=False)
        avg_strip(p, res, [("dies before 14:00", "early_death", None),
                           ("solo deaths", "solo_deaths", None),
                           ("deaths <14:00", "deaths_pre14", None),
                           ("kills <14:00", "kills_pre14", None),
                           ("lane diffs", None, "±0 (even)")])
        cols = [Col("icon", "", 0, kind="icon"),
                Col("name", "ROLE / CHAMPION", 15, "w", stretch=True,
                    sort=lambda r: r.extra["order"]),
                Col("n", "GAMES", 6, "e", sort=lambda r: r.data.games)]
        for k, t in zip(DIFFS, ("CS@10", "CS@15", "GOLD@10", "GOLD@15",
                                "XP@15")):
            cols.append(Col(k, t, 11, "e",
                            sort=lambda r, k=k: r.data.diffs[k].mean))
        cols += [Col("ed", "DIES <14:00", 13, "e",
                     sort=lambda r: r.data.early_death_rate.p,
                     tip="Share of games with at least one death before "
                     "14:00 (Wilson 90%)"),
                 Col("solo", "SOLO DEATHS", 12, "e",
                     sort=lambda r: r.data.solo_deaths.mean)]
        rows = []
        order = 0
        for role, ls in res.lane.items():
            rows.append(self._row(ROLE_LABEL[role], None, ls, order, True))
            order += 1
        for champ, ls in sorted(res.lane_by_champion.items(),
                                key=lambda kv: -kv[1].games):
            rows.append(self._row(cname(champ), champ, ls, order, False))
            order += 1
        t = Table(p, cols, icon_loader=self.app.icons.get,
                  rows_visible=min(max(len(rows), 3), 10), tooltip=self.tip,
                  default_sort="name",
                  on_click=lambda r: r.icon and not r.icon.startswith(
                      "role:") and self.app.set_champion(r.icon))
        t.sort_desc = False
        t.set_rows(rows)
        t.pack(fill="x")

        # --- lane curves
        p, _ = panel(b, "LANE CURVES", EDGE_HI, right="wins teal · losses "
                     "red · dashed = all", expand=False, pady=(px(8), 0))
        cs = grid(p, 3, bg=PANEL)
        for col, metric in zip(cs, ("gold", "cs", "xp")):
            C.lane_curve(col, self, res, metric, height=180)

        # --- what lane state wins games + early deaths
        left, right = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(left, "WIN RATE BY LANE STATE", EDGE_HI, right="",
                     expand=False)
        C.wr_bars(p, self, res, "gd15", "BY GOLD DIFF @15", T.wr_gd15)
        C.wr_bars(p, self, res, "cs10", "BY CS DIFF @10", T.wr_cs10)
        p, _ = panel(right, "EARLY DEATHS (BEFORE 14:00)", BAD, right="",
                     expand=False)
        self._early(p, res)

        p, _ = panel(b, "MATCHUPS", EDGE_HI, right="2+ games vs the same "
                     "laner", expand=False, pady=(px(8), 0))
        if res.matchups:
            C.matchup_table(p, self, res)
        else:
            empty_note(p, "No lane opponent repeats yet.")

    def _row(self, name, champ, ls, order, marked):
        cells = {"name": name, "n": str(ls.games)}
        for k in DIFFS:
            m = ls.diffs[k]
            cells[k] = (mean_pm(k, m), signed_color(m.mean))
        r = ls.early_death_rate
        cells["ed"] = (rate_pm(r), BAD if r.p > 0.5 else FG)
        cells["solo"] = (mean_pm("solo_deaths", ls.solo_deaths), FG)
        return Row(cells, icon=champ or "role:" + ls.role, marked=marked,
                   data=ls,
                   extra={"order": order})

    def _early(self, p, res):
        ls = list(res.lane.values())
        if not ls:
            empty_note(p, "No lane games.")
            return
        # pool across roles shown
        n = sum(x.games for x in ls)
        ed_w = sum(x.early_death_rate.wins for x in ls)
        from ...analysis.stats import Rate
        rate = Rate(ed_w, n)
        zones = {}
        for x in ls:
            for z, c in x.early_death_zones:
                zones[z] = zones.get(z, 0) + c
        total = sum(zones.values())
        top = tk.Frame(p, bg=PANEL)
        top.pack(fill="x")
        from .base import big_number
        big_number(top, "GAMES WITH AN EARLY DEATH", rate_pm(rate),
                   BAD if rate.p > 0.5 else FG, "n=%d games" % n).pack(
                       side="left")
        fb_w = sum(x.fb_involved.wins for x in ls)
        big_number(top, "FIRST BLOOD INVOLVEMENT", rate_pm(Rate(fb_w, n)),
                   FG, "kill or assist on first blood").pack(
                       side="left", padx=(px(26), 0))
        tk.Label(p, text="WHERE EARLY DEATHS HAPPEN", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w",
                                                     pady=(px(10), px(2)))
        if not total:
            empty_note(p, "No deaths before 14:00 — nice.")
        for z, c in sorted(zones.items(), key=lambda kv: -kv[1])[:6]:
            rowf = tk.Frame(p, bg=PANEL)
            rowf.pack(fill="x", pady=1)
            tk.Label(rowf, text=z, bg=PANEL, fg=FG, font=F_SM, width=22,
                     anchor="w").pack(side="left")
            bar = tk.Canvas(rowf, bg=PANEL, highlightthickness=0,
                            height=px(10), width=1)
            bar.pack(side="left", fill="x", expand=True, padx=(px(6), px(6)))
            frac = c / total
            bar.bind("<Configure>", lambda e, b=bar, f=frac: (
                b.delete("all"), b.create_rectangle(0, 1, e.width * f,
                                                    e.height - 1, fill=BAD,
                                                    width=0)))
            tk.Label(rowf, text="%d (%.0f%%)" % (c, 100 * frac), bg=PANEL,
                     fg=DIM, font=("Segoe UI", 8), width=10,
                     anchor="e").pack(side="left")
        C.wr_bars(p, self, res, "early_deaths",
                  "WIN RATE BY DEATHS BEFORE 14:00", T.wr_early_deaths,
                  height=150)
