"""Match list (every game as a compact row with a colour-scaled game score)
and the single-game page: header, gold/CS/XP diff curves vs the lane
opponent, death markers on the minimap, that game's radar and the
scoreboard."""
from __future__ import annotations

import threading
import tkinter as tk

from ...analysis import ROLE_LABEL
from ...analysis.radar import AXIS_LABEL, SCORED
from ...pipeline import load_features
from ...theme import widgets as W
from ...theme.charts import LineChart, MapChart, Series
from ...theme.radar import RadarAxis, RadarChart
from ...theme.tokens import (BAD, DIM, DMG_AD, DMG_AP, EDGE_HI, F_LG_B, F_SM,
                             F_SM_B, F_XS, F_XS_B, FG, GHOST, GOOD, PANEL,
                             PANEL2, score_color, signed_color)
from ...theme.widgets import Col, Row, Table, px
from .base import (Screen, chart_block, clear, cname, date_text, dur_text,
                   empty_note, grid, panel)

MAP_MAX = 14870.0


class MatchesScreen(Screen):
    def build(self):
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)
        self.game_id = None
        self._imgs = []

    def update_result(self, res):
        self.res = res
        if self.game_id and any(g.match_id == self.game_id
                                for g in res.games):
            self.open_game(self.game_id)
        else:
            self.game_id = None
            self._list()

    def snapshot_action(self, extra):
        if extra == "game" and self.res.games:
            self.open_game(self.res.games[0].match_id)

    # ------------------------------------------------------------- list
    def _list(self):
        res = self.res
        clear(self.box)
        p, _ = panel(self.box, "MATCHES", EDGE_HI, right="%d games · newest "
                     "first · click a game to open it" % len(res.games))
        cols = [Col("icon", "", 0, kind="icon"),
                Col("champ", "CHAMPION", 12, "w",
                    sort=lambda r: r.data.champion),
                Col("res", "RESULT", 6, "center", sort=lambda r: r.data.win),
                Col("role", "ROLE", 7, "w", sort=lambda r: r.data.role or ""),
                Col("vs", "VS", 11, "w", stretch=True,
                    sort=lambda r: r.data.opponent or ""),
                Col("kda", "K / D / A", 9, "center",
                    sort=lambda r: (r.data.k + r.data.a) / max(r.data.d, 1)),
                Col("cs", "CS (/MIN)", 10, "e", sort=lambda r: r.data.cs),
                Col("gd", "GD@15", 8, "e", sort=lambda r: r.data.gd15),
                Col("len", "LENGTH", 7, "e",
                    sort=lambda r: r.data.duration_s),
                Col("date", "DATE", 13, "e",
                    sort=lambda r: r.data.creation_ms),
                Col("sc", "SCORE", 7, "e", sort=lambda r: r.data.score),
                Col("bar", "", 10, kind="bars")]
        t = Table(p, cols, icon_loader=self.app.icons.get, rows_visible=None,
                  tooltip=self.tip, default_sort="date",
                  on_click=lambda r: self.open_game(r.data.match_id))
        t.body.configure(height=t.row_h * max(len(res.games), 1))
        rows = []
        for g in res.games:
            sc = g.score
            rows.append(Row({
                "champ": cname(g.champion),
                "res": ("WIN" if g.win else "LOSS", GOOD if g.win else BAD),
                "role": (ROLE_LABEL.get(g.role, "—"), DIM),
                "vs": (cname(g.opponent) if g.opponent else "—", DIM),
                "kda": "%d / %d / %d" % (g.k, g.d, g.a),
                "cs": "%d (%.1f)" % (g.cs, g.cs_per_min),
                "gd": ("%+.0f" % g.gd15 if g.gd15 == g.gd15 else "—",
                       signed_color(g.gd15)),
                "len": (dur_text(g.duration_s), DIM),
                "date": (date_text(g.creation_ms), DIM),
                "sc": ("%.0f" % sc if sc == sc else "—", score_color(sc)),
                "bar": [(max(0, min(sc, 100)) / 100, score_color(sc))]
                if sc == sc else []}, icon=g.champion, data=g))
        t.set_rows(rows)
        t.pack(fill="both", expand=True)

    # ------------------------------------------------------------- game
    def open_game(self, match_id):
        self.game_id = match_id
        clear(self.box)
        res = self.res
        g = next((x for x in res.games if x.match_id == match_id), None)
        if g is None:
            self._list()
            return
        bar = tk.Frame(self.box, bg=self.box["bg"])
        bar.pack(fill="x", pady=(0, px(6)))
        W.button(bar, "‹ All matches", self._back, fg=DIM, font=F_SM).pack(
            side="left")
        tk.Label(bar, text=match_id, bg=self.box["bg"], fg=DIM,
                 font=F_XS).pack(side="right")
        self.page = tk.Frame(self.box, bg=self.box["bg"])
        self.page.pack(fill="both", expand=True)
        tk.Label(self.page, text="Loading game…", bg=self.box["bg"], fg=DIM,
                 font=F_SM).pack(anchor="w")
        app = self.app

        def work():
            try:
                mf = load_features(app.cache, [match_id], app.legendary)
                mf = mf[0] if mf else None
            except Exception:       # noqa: BLE001
                mf = None
            app.post(self._game_page, g, mf)
        threading.Thread(target=work, daemon=True).start()
        self.area.to_top()

    def _back(self):
        self.game_id = None
        self._list()

    def _game_page(self, g, mf):
        if self.game_id != g.match_id or not self.page.winfo_exists():
            return
        clear(self.page)
        self._imgs = []
        page = self.page
        me = None
        if mf is not None:
            me = next((p for p in mf.players
                       if p.puuid == self.res.puuid), None)
        # header
        hp = W.CutPanel(page, padx=14, fill=True)
        hp.pack(fill="x")
        h = hp.inner
        ic = self.app.icons.get(g.champion, px(44), lambda: None)
        if ic is not None:
            self._imgs.append(ic)
            tk.Label(h, image=ic, bg=PANEL).pack(side="left",
                                                 padx=(0, px(12)))
        who = tk.Frame(h, bg=PANEL)
        who.pack(side="left")
        tk.Label(who, text="%s  %s" % (cname(g.champion), "VICTORY" if g.win
                                       else "DEFEAT"), bg=PANEL,
                 fg=GOOD if g.win else BAD, font=F_LG_B).pack(anchor="w")
        tk.Label(who, text="%s%s · %s · %s · patch %s" % (
            ROLE_LABEL.get(g.role, "?"),
            (" vs " + cname(g.opponent)) if g.opponent else "",
            dur_text(g.duration_s), date_text(g.creation_ms), g.patch),
            bg=PANEL, fg=DIM, font=F_SM).pack(anchor="w")
        for lab, val, col in (
                ("SCORE", "%.0f" % g.score if g.score == g.score else "—",
                 score_color(g.score)),
                ("K / D / A", "%d / %d / %d" % (g.k, g.d, g.a), FG),
                ("CS", "%d  (%.1f/min)" % (g.cs, g.cs_per_min), FG),
                ("GD@15", "%+.0f" % g.gd15 if g.gd15 == g.gd15 else "—",
                 signed_color(g.gd15))):
            f = tk.Frame(h, bg=PANEL)
            f.pack(side="right", padx=(px(22), 0))
            tk.Label(f, text=lab, bg=PANEL, fg=DIM, font=F_XS_B).pack(
                anchor="e")
            tk.Label(f, text=val, bg=PANEL, fg=col, font=F_LG_B).pack(
                anchor="e")
        if me is None:
            p, _ = panel(page, "GAME DETAIL", EDGE_HI, expand=False,
                         pady=(px(8), 0))
            empty_note(p, "This game's details aren't in the cache.")
            return

        left, right = grid(page, 2, pady=(px(8), 0))
        p, _ = panel(left, "LANE DIFF VS %s" % (
            cname(me.opponent_champion).upper() if me.opponent_champion
            else "LANE OPPONENT"), EDGE_HI, right="per minute",
            expand=False)
        if not mf.has_timeline:
            empty_note(p, "No timeline for this game — curves and death "
                       "locations need it.")
        for metric, title, col in (("gold", "GOLD DIFF", EDGE_HI),
                                   ("cs", "CS DIFF", GOOD),
                                   ("xp", "XP DIFF", DMG_AP)):
            ys = me.curves.get(metric) or []
            if not ys:
                continue
            ch = chart_block(p, LineChart, title, height=130, tip=self.tip)
            f = (lambda v: "%+.1f" % v) if metric == "cs" else (
                lambda v: "%+.0f" % v)
            ch.set(xlabel="game time",
                   ylabel="%s vs lane opponent" % title.split()[0].lower()
                   .replace("cs", "CS").replace("xp", "XP"),
                   series=[Series(list(range(len(ys))), ys, col, title.title(),
                                  fmt=f)], baseline=0.0, yfmt=f,
                   xfmt=lambda v: "%d:00" % v)
            end = ys[min(15, len(ys) - 1)]
            ch.meta("1 game · %d minutes" % (len(ys) - 1),
                    "%s at %d:00: %s." % (title.capitalize(),
                                           min(15, len(ys) - 1), f(end)))
        p, _ = panel(right, "THIS GAME'S RADAR", EDGE_HI,
                     right="7 axes · consistency needs many games",
                     expand=False)
        vals = self.res.game_axes.get(g.match_id)
        if vals:
            rc = RadarChart(p, self.tip, height=300)
            from .base import peer_word
            rc.peer_word = peer_word(self.res, g.role)
            rc.scale = (0, 100)     # one game swings 5..95: full scale
            rc.pack(fill="x")
            rc.set_radar([RadarAxis(ax, AXIS_LABEL[ax], v, n=1)
                          for ax, v in zip(SCORED, vals[:len(SCORED)])],
                         animate=False)
        else:
            empty_note(p, "No radar for this game.")

        left, right = grid(page, 2, pady=(px(8), 0))
        p, _ = panel(left, "DEATHS ON THE MAP", BAD,
                     right="%d deaths" % len(me.deaths), expand=False)
        mc = chart_block(p, MapChart, "WHERE YOU DIED", height=330,
                         tip=self.tip)
        mc.minimap = self.app.icons.minimap()
        markers = []
        for i, d in enumerate(me.deaths):
            x, y = d.x, d.y
            tags = [t for t, on in (("solo", d.solo),
                                    ("while ahead", d.while_ahead),
                                    ("→ enemy objective",
                                     d.before_enemy_objective)) if on]
            label = "%d:%02d" % (int(d.t), int(d.t % 1 * 60))
            markers.append((x, y, label, BAD if tags else EDGE_HI))
        mc.set(points=[], heat=False, markers=markers)
        mc.meta("n=%d deaths · true map orientation (%s side)" % (
            len(me.deaths), "blue" if me.team == 100 else "red"),
            "; ".join("%d:%02d%s" % (int(d.t), int(d.t % 1 * 60),
                                     " by " + cname(d.killer_champion)
                                     if d.killer_champion else "")
                      for d in me.deaths[:8]) or "No deaths this game.")
        p, _ = panel(right, "SCOREBOARD", EDGE_HI, right="", expand=False)
        self._scoreboard(p, mf, me)

    def _scoreboard(self, p, mf, me):
        for team, col, name in ((me.team, GOOD, "YOUR TEAM"),
                                (300 - me.team, BAD, "ENEMY TEAM")):
            tk.Label(p, text=name, bg=PANEL, fg=col, font=F_XS_B).pack(
                anchor="w", pady=(px(6), px(2)))
            for q in sorted((q for q in mf.players if q.team == team),
                            key=lambda q: ["TOP", "JUNGLE", "MIDDLE",
                                           "BOTTOM", "UTILITY"].index(q.role)
                            if q.role in ROLE_LABEL else 9):
                r = tk.Frame(p, bg=PANEL2 if q.puuid != me.puuid else
                             "#13233a")
                r.pack(fill="x", pady=1)
                ic = self.app.icons.get(q.champion, px(20), lambda: None)
                if ic is not None:
                    self._imgs.append(ic)
                    tk.Label(r, image=ic, bg=r["bg"]).pack(side="left",
                                                           padx=px(3))
                nm = q.riot_id.split("#")[0] if q.riot_id else "?"
                tk.Label(r, text=("◆ " if q.puuid == me.puuid else "") + nm,
                         bg=r["bg"], fg=EDGE_HI if q.puuid == me.puuid
                         else FG, font=F_XS, width=18, anchor="w").pack(
                             side="left")
                s = q.stats
                tk.Label(r, text="%d / %d / %d" % (s["kills"], s["deaths"],
                                                  s["assists"]),
                         bg=r["bg"], fg=FG, font=F_XS, width=10).pack(
                             side="left")
                tk.Label(r, text="%d cs" % s["cs"], bg=r["bg"], fg=DIM,
                         font=F_XS, width=7).pack(side="left")
                dmg = s.get("dmg_share", float("nan"))
                tk.Label(r, text="%.0f%% dmg" % (100 * dmg) if dmg == dmg
                         else "", bg=r["bg"], fg=DIM, font=F_XS,
                         width=8).pack(side="left")
