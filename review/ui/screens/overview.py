"""Overview: header (record, WR ±, games, overall score + sparkline), the
performance radar as the centrepiece with key stats vs peers beside it, and
STRENGTHS (teal) / WEAKNESSES (red) side by side like DRAFT's teams."""
from __future__ import annotations

import math
import tkinter as tk

from ...analysis.metrics import HELP
from ...analysis import ROLE_LABEL, fmt_value
from ...theme import anim
from ...theme import widgets as W
from ...theme.charts import Sparkline
from ...theme.radar import Overlay, RadarChart
from ...theme.tokens import (BAD, DIM, EDGE_HI, F_LG_B, F_NUM, F_SM,
                             F_XS, F_XS_B, FG, GHOST, GOOD, PANEL, PANEL2,
                             SP_M, SP_S, SP_XS, signed_color)
from ...theme.widgets import px
from .base import (avg_text, Screen, clear, empty_note, fifty_means, finding_card,
                   group_name, panel, peer_word, pm_text, radar_axes, row,
                   score_pm)


class OverviewScreen(Screen):
    def build(self):
        b = self.body
        hp = W.CutPanel(b, padx=14, fill=True)
        hp.pack(fill="x")
        h = hp.inner
        who = tk.Frame(h, bg=PANEL)
        who.pack(side="left", padx=(0, px(24)))
        self.name = tk.Label(who, text="", bg=PANEL, fg=FG, font=F_LG_B)
        self.name.pack(anchor="w")
        self.rank = tk.Label(who, text="", bg=PANEL, fg=DIM, font=F_SM)
        self.rank.pack(anchor="w")

        def block(caption):
            f = tk.Frame(h, bg=PANEL)
            f.pack(side="left", padx=(px(18), 0))
            tk.Label(f, text=caption, bg=PANEL, fg=DIM, font=F_XS_B).pack(
                anchor="w")
            return f
        f = block("WIN RATE")
        line = tk.Frame(f, bg=PANEL)
        line.pack(anchor="w")
        self.wr = tk.Label(line, text="", bg=PANEL, fg=FG, font=F_LG_B)
        self.wr.pack(side="left")
        self.wr_pm = tk.Label(line, text="", bg=PANEL, fg=DIM, font=F_SM)
        self.wr_pm.pack(side="left", padx=(SP_S, 0), anchor="s",
                        pady=(0, px(2)))
        self.split = W.SplitBar(f, width=110, height=8)
        self.split.pack(anchor="w", pady=(SP_XS, 0))
        f = block("GAMES")
        self.games = tk.Label(f, text="", bg=PANEL, fg=FG, font=F_LG_B)
        self.games.pack(anchor="w")
        self.rec = tk.Label(f, text="", bg=PANEL, fg=DIM, font=F_XS)
        self.rec.pack(anchor="w")
        sc = tk.Frame(h, bg=PANEL)
        sc.pack(side="right")
        self.score_cap = tk.Label(sc, text="OVERALL SCORE", bg=PANEL,
                                  fg=DIM, font=F_XS_B)
        self.score_cap.pack(anchor="e")
        sl = tk.Frame(sc, bg=PANEL)
        sl.pack(anchor="e")
        self.spark = Sparkline(sl, self.tip, width=120, height=34)
        self.spark.pack(side="left", padx=(0, SP_M))
        self.score = tk.Label(sl, text="", bg=PANEL, fg=EDGE_HI, font=F_NUM)
        self.score.pack(side="left")
        self.score_pm = tk.Label(sl, text="", bg=PANEL, fg=DIM, font=F_SM)
        self.score_pm.pack(side="left", anchor="s", pady=(0, px(6)),
                           padx=(SP_XS, 0))
        self.tip.bind(self.score, "The average of your 8 radar scores. 50 = "
                      "a typical player in your role; ± is how far it could "
                      "be off with this many games.")

        mid = row(b, pady=(SP_M, 0))
        # key stats first (packed right) so the radar can never squeeze it
        kp, self.key_n = panel(mid, "KEY STATS", EDGE_HI, right="",
                               side="right", expand=False, padx=(px(8), 0),
                               fit=True, min_width=330)
        self.keys = tk.Frame(kp, bg=PANEL)
        self.keys.pack(fill="both", expand=True)
        rp, self.radar_n = panel(mid, "PERFORMANCE", EDGE_HI, right="",
                                 side="left")
        self.radar = RadarChart(rp, self.tip, height=440,
                                on_axis=lambda k: self.app.navigate(
                                    "radar", axis=k))
        self.radar.pack(fill="x")
        self.legend = tk.Label(rp, text="", bg=PANEL, fg=DIM, font=F_XS,
                               justify="left", anchor="w", wraplength=px(400))
        self.legend.pack(fill="x")
        self.legend.bind("<Configure>", lambda e: self.legend.configure(
            wraplength=max(e.width - 4, 120)))

        bot = row(b, pady=(SP_M, 0))
        sp, self.s_n = panel(bot, "STRENGTHS", GOOD, right="", side="left")
        self.s_box = tk.Frame(sp, bg=PANEL)
        self.s_box.pack(fill="both", expand=True)
        wp, self.w_n = panel(bot, "WEAKNESSES", BAD, right="", side="left",
                             padx=(px(8), 0))
        self.w_box = tk.Frame(wp, bg=PANEL)
        self.w_box.pack(fill="both", expand=True)

    def update_result(self, res):
        p = self.app.player
        self.name.configure(text=p["riot_id"])
        self.rank.configure(text="%s · %s" % (p.get("rank", ""), p["region"]))
        r = res.record
        self.wr.configure(text="%.1f%%" % (100 * r.p) if r.n else "—",
                          fg=GOOD if r.p > 0.52 else BAD if r.p < 0.48 else FG)
        self.wr_pm.configure(text=pm_text(r))
        self.split.set(r.p if r.n else None)
        self.games.configure(text="%d" % res.n_games)
        k = res.excluded.remakes
        self.rec.configure(text="%dW %dL%s" % (
            r.wins, r.n - r.wins, "" if not k else
            " · %d remake%s / AFK game%s left out" % (
                k, "" if k == 1 else "s", "" if k == 1 else "s")))
        R = res.radar
        if R:
            anim.count_up(self.score, R.overall, "%.0f")
            self.score_pm.configure(text=score_pm(R))
            self.spark.set(ys=[m for _, m, _, _ in res.score_series],
                           tip="Game score, average of the last 20 games (n=%d)"
                           % res.n_games)
            ov = []
            if res.previous_radar:
                ov.append(Overlay([a.score for a in res.previous_radar.axes],
                                  GHOST, "first half"))
            role = res.dist_role
            self.radar.peer_word = peer_word(res, role)
            self.radar.set_radar(radar_axes(R), ov)
            vs = res.reference_label
            self.radar_n.configure(text="%sn=%d%s" % (
                "VS %s · " % vs.upper() if vs else "",
                R.n_games, " · LOW SAMPLE" if R.insufficient else ""),
                fg=GOOD if vs else DIM)
            self.score_cap.configure(
                text="OVERALL SCORE · VS %s" % vs.upper() if vs
                else "OVERALL SCORE", fg=GOOD if vs else DIM)
            self.legend.configure(text="◆ you   - - first half   ○ " +
                                  fifty_means(res, role))
        self._keys(res)
        self._findings(res)

    def prepare_reveal(self):
        super().prepare_reveal()
        self.score.configure(text="50")      # counts up from here

    def on_reveal(self):
        super().on_reveal()
        res = getattr(self.app, "result", None)
        if res and res.radar:
            anim.count_up(self.score, res.radar.overall, "%.0f")

    def _keys(self, res):
        clear(self.keys)
        for i, (k, label, m, peer, pol) in enumerate(res.key_stats):
            t = tk.Frame(self.keys, bg=PANEL2, padx=px(8), pady=px(5))
            t.grid(row=i // 2, column=i % 2, sticky="nsew", padx=1, pady=1)
            tk.Label(t, text=label.upper(), bg=PANEL2, fg=DIM,
                     font=F_XS_B).pack(anchor="w")
            diff = m.mean - peer if not (math.isnan(m.mean) or
                                         math.isnan(peer)) else float("nan")
            # within 3% of the peer value reads as "level" (parchment)
            dead = max(abs(peer) * 0.03, 1e-9) if not math.isnan(peer) else 0
            col = DIM if math.isnan(diff) else signed_color(
                diff * pol if pol else float("nan"), dead=dead)
            tk.Label(t, text=fmt_value(k, m.mean), bg=PANEL2, fg=col,
                     font=("Segoe UI", 12, "bold")).pack(anchor="w")
            tk.Label(t, text="%s %s · n=%d" % (group_name(res),
                                               avg_text(k, peer), m.n),
                     bg=PANEL2, fg=DIM, font=F_XS).pack(anchor="w")
            tip = HELP.get(k)
            if tip:
                for w in (t, *t.winfo_children()):
                    w.bind("<Enter>", lambda e, s=tip: self.tip.show(
                        e.x_root, e.y_root, s))
                    w.bind("<Leave>", lambda e: self.tip.hide())
        for c in range(2):
            self.keys.grid_columnconfigure(c, weight=1, minsize=px(150))
        self.key_n.configure(text="vs %s · same role" % (
            res.reference_label or "players in your games"))

    def _findings(self, res):
        for box, items, col, lbl in ((self.s_box, res.strengths, GOOD,
                                      self.s_n),
                                     (self.w_box, res.weaknesses, BAD,
                                      self.w_n)):
            clear(box)
            lbl.configure(text="%d found" % len(items))
            if not items:
                empty_note(box, "Nothing stands out clearly yet. A "
                           "strength or weakness needs at least 20 games and "
                           "a difference too big to be chance — more games "
                           "will sharpen this.")
            for f in items:
                finding_card(box, f, col, app=self.app,
                             group=group_name(res))
