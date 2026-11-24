"""Deaths: Summoner's Rift minimap heatmap of where you die, filterable by
game phase (0–14, 14–25, 25+), with death context (followed by an enemy
objective, while ahead, solo), zones per phase and deaths by minute."""
from __future__ import annotations

import threading
import tkinter as tk

from ...analysis.aggregates import PHASES
from ...theme import widgets as W
from ...theme.tokens import BAD, DIM, EDGE_HI, F_SM, FG, PANEL
from ...theme.widgets import px
from .. import catalogue as C
from .base import (Screen, avg_word, big_number, clear, empty_note, grid,
                   mean_pm, panel, rate_pm)


class DeathsScreen(Screen):
    def build(self):
        self.phase = None
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)
        # the minimap is a download on first use: fetch it off the UI thread
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        try:
            self.app.icons.minimap()
        except Exception:
            pass

    def update_result(self, res):
        self.res = res
        self._render()

    def _pick(self, ph):
        self.phase = ph
        self._render()

    def _render(self):
        res = self.res
        d = res.deaths
        clear(self.box)
        b = self.box
        p, _ = panel(b, "DEATHS", BAD, right="n=%d deaths in %d games" % (
            len(d.points), res.n_games), expand=False)
        big = tk.Frame(p, bg=PANEL)
        big.pack(fill="x", pady=(0, px(6)))
        pm = getattr(res, "peer_means", {}) or {}
        items = [("PER GAME", mean_pm("deaths", d.per_game), FG,
                  ("%s %.1f" % (avg_word(res), pm["deaths"]))
                  if "deaths" in pm else "average ± likely range"),
                 ("FOLLOWED BY ENEMY OBJECTIVE", rate_pm(
                     d.followed_by_objective), BAD
                  if d.followed_by_objective.p > 0.3 else FG,
                  "dragon/Herald/Baron/tower within 60 s"),
                 ("WHILE AHEAD IN LANE", rate_pm(d.while_ahead), FG,
                  "≥ +300 gold vs your opponent"),
                 ("SOLO DEATHS", rate_pm(d.solo), FG,
                  "one enemy, no assists")]
        for i, (lab, val, col, sub) in enumerate(items):
            big_number(big, lab, val, col, sub).pack(
                side="left", padx=(0 if i == 0 else px(26), 0))

        left, right = grid(b, 2, pady=(px(8), 0))
        p, _ = panel(left, "WHERE YOU DIE", EDGE_HI, right="", expand=False)
        tabs = W.TabRow(p, "PHASE", [(None, "All")] + [
            (ph, ph + " min") for ph, _, _ in PHASES], self.phase,
            on_change=self._pick, style="teal", bg=PANEL, small=True)
        tabs.pack(anchor="w", pady=(0, px(4)))
        C.death_map(p, self, res, self.phase, height=420)

        p, _ = panel(right, "ZONES", EDGE_HI, right=("phase %s min" %
                                                    self.phase)
                     if self.phase else "all phases", expand=False)
        zones = {}
        for ph, z in d.zones_by_phase.items():
            if self.phase and ph != self.phase:
                continue
            for name, c in z:
                zones[name] = zones.get(name, 0) + c
        total = sum(zones.values())
        if not total:
            empty_note(p, "No deaths in this phase.")
        for name, c in sorted(zones.items(), key=lambda kv: -kv[1])[:8]:
            r = tk.Frame(p, bg=PANEL)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=name, bg=PANEL, fg=FG, font=F_SM, width=22,
                     anchor="w").pack(side="left")
            bar = tk.Canvas(r, bg=PANEL, highlightthickness=0, height=px(10),
                            width=1)
            bar.pack(side="left", fill="x", expand=True, padx=px(6))
            f = c / total
            bar.bind("<Configure>", lambda e, bb=bar, f=f: (
                bb.delete("all"), bb.create_rectangle(
                    0, 1, e.width * f, e.height - 1, fill=BAD, width=0)))
            tk.Label(r, text="%d (%.0f%%)" % (c, 100 * f), bg=PANEL, fg=DIM,
                     font=("Segoe UI", 8), width=10, anchor="e").pack(
                         side="left")
        C.death_minutes(p, self, res, height=180)
