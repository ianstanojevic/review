"""Radar view: the large performance radar with overlays (previous period,
champion vs overall, role vs role, compare another Riot ID), a drill-down
for the selected axis (sub-metrics vs peers, trends over time, best and
worst games, one concrete tip) and small multiples per role / champion."""
from __future__ import annotations

import math
import threading
import tkinter as tk

from ...analysis import ROLE_LABEL, Filter, fmt_value
from ...analysis.findings import TIPS_STRONG, TIPS_STRONG_KEY, TIPS_WEAK
from ...analysis.metrics import METRICS
from ...analysis.radar import AXES, AXIS_LABEL, SCORED
from ...analysis.stats import rolling_mean
from ...theme import widgets as W
from ...theme.charts import LineChart, Series
from ...theme.radar import Overlay, RadarChart, thumbnail
from ...theme.tokens import (BAD, DIM, DMG_AD, DMG_AP, DMG_TRUE, EDGE_HI,
                             F_LG_B, F_SM, F_SM_B, F_XS, F_XS_B, FG, GHOST,
                             GOOD, HOVER, PANEL, PANEL2, score_color)
from ...theme.widgets import Col, Row, Table, px
from ...analysis.metrics import HELP
from .base import (Screen, chart_block, clear, cname, date_text, empty_note,
                   fit_wrap,
                   fifty_means, group_name, panel, peer_word, radar_axes,
                   score_pm)

SUB_COLORS = [GOOD, DMG_AP, DMG_AD, DMG_TRUE, GHOST, "#9b7fd1"]
MODES = [("none", "Just me"), ("previous", "Previous"),
         ("champion", "Champion"), ("role", "Role"),
         ("compare", "Compare"), ("scales", "Both scales")]
CONSISTENCY_TIP = ("Your games swing a lot: pick 2–3 champions for ranked, "
                   "and stop after two losses in a row — the data on your "
                   "games after losses is in Stats lab › Sessions.")


def axis_tip(axis) -> str:
    if axis.axis == "consistency":
        return CONSISTENCY_TIP if axis.score < 50 else (
            "You're steady from game to game — that reliability is a win "
            "condition; protect it by queueing when you're fresh.")
    subs = [s for s in axis.subs if s.score == s.score]
    if not subs:
        return ""
    if axis.score < 50:
        for s in sorted(subs, key=lambda s: s.score):
            if s.key in TIPS_WEAK:
                return TIPS_WEAK[s.key]
    for s in sorted(subs, key=lambda s: -s.score):
        if s.key in TIPS_STRONG_KEY:
            return TIPS_STRONG_KEY[s.key]
        fam = METRICS[s.key].family if s.key in METRICS else ""
        if fam in TIPS_STRONG:
            return TIPS_STRONG[fam]
    return ""


class RadarScreen(Screen):
    def build(self):
        self.mode = "previous"
        self.sel_champ = self.sel_role = None
        self.axis = None
        self.compare = None          # {"riot_id", "puuid", "radar"}
        self._cmp_job = None
        self._imgs = []
        b = self.body
        top = tk.Frame(b, bg=b["bg"])
        top.pack(fill="both", expand=True)
        # drill-down on the right (packed first so it keeps its width)
        self.dd_outer = W.CutPanel(top, padx=14)
        self.dd_outer.pack(side="right", fill="y", padx=(px(8), 0))
        tk.Frame(self.dd_outer.inner, bg=PANEL, width=px(420),
                 height=0).pack()
        self.dd = tk.Frame(self.dd_outer.inner, bg=PANEL)
        self.dd.pack(fill="both", expand=True)
        lp, self.n_lbl = panel(top, "PERFORMANCE RADAR", EDGE_HI, right="",
                               side="left")
        self.modes = W.TabRow(lp, "OVERLAY", MODES, self.mode,
                              on_change=self._set_mode, style="gold",
                              bg=PANEL, small=True)
        self.modes.pack(anchor="w")
        self.sub = tk.Frame(lp, bg=PANEL)
        self.sub.pack(fill="x", pady=(px(6), 0))
        self.radar = RadarChart(lp, self.tip, height=520,
                                on_axis=self._select_axis)
        self.radar.pack(fill="x", pady=(px(4), 0))
        self.legend = tk.Label(lp, text="", bg=PANEL, fg=DIM, font=F_XS,
                               anchor="w", justify="left")
        self.legend.pack(fill="x")
        self.legend.bind("<Configure>", lambda e: self.legend.configure(
            wraplength=max(e.width - 4, 120)))
        self.sm_panel, self.sm_n = panel(b, "SMALL MULTIPLES", EDGE_HI,
                                         right="", expand=False,
                                         pady=(px(8), 0))
        self.sm = tk.Frame(self.sm_panel, bg=PANEL)
        self.sm.pack(fill="x")

    # ------------------------------------------------------------ public
    def update_result(self, res):
        self.res = res
        R = res.radar
        if R is None:
            return
        if self.axis is None:
            self.axis = min(R.axes, key=lambda a: a.score).axis
        self.radar.selected = self.axis
        self.radar.peer_word = peer_word(res, res.dist_role)
        vs = res.reference_label
        self.n_lbl.configure(text="%sn=%d · overall %.0f %s · 90%% CI%s" % (
            "VS %s · " % vs.upper() if vs else "",
            R.n_games, R.overall, score_pm(R),
            " · LOW SAMPLE" if R.insufficient else ""),
            fg=GOOD if vs else DIM)
        self.other_scale = None
        if self.mode == "scales":
            self._run_other_scale()
        if self.compare:
            self._run_compare(self.compare["riot_id"], quiet=True)
        self._sub_row()
        self._draw()
        self._drill()
        self._multiples()

    def focus(self, axis=None, **_):
        if axis:
            self._select_axis(axis)

    def snapshot_action(self, extra):
        k, _, v = extra.partition("=")
        if k == "axis":
            self._select_axis(v)
        elif k == "mode":
            self._set_mode(v)
            self.modes.select(v, fire=False)

    # ----------------------------------------------------------- overlays
    def _set_mode(self, mode):
        self.mode = mode
        if mode == "scales" and getattr(self, "other_scale", None) is None:
            self._run_other_scale()
        self._sub_row()
        self._draw()

    def _other_reference(self):
        """The scale NOT currently shown: My games <-> a benchmark."""
        from ...benchmark import LEVELS, MIN_USABLE
        app = self.app
        if app.current_reference():
            return None, "my games"
        region = app.player["region"]
        for lv in reversed(list(LEVELS)):          # highest first
            if app.cache.benchmark_info(region, lv)[0] >= MIN_USABLE:
                return (region, lv), LEVELS[lv].label
        return False, None

    def _run_other_scale(self):
        """Score the same games on the other scale, in a worker thread."""
        from ...pipeline import analyze_player
        ref, label = self._other_reference()
        if ref is False:
            self.other_scale = {"missing": True}
            return
        app, flt = self.app, self.app.current_filter()
        self.other_scale = {"pending": True, "label": label}
        token = object()
        self._scale_token = token

        def work():
            try:
                r = analyze_player(app.cache, app.player["puuid"], flt,
                                   app.legendary, limit=app.count,
                                   reference=ref)
            except Exception as e:      # noqa: BLE001
                app._log(e)
                return

            def done():
                if self._scale_token is token:
                    self.other_scale = {"radar": r.radar, "label": label}
                    if self.mode == "scales":
                        self._draw()
            app.post(done)
        threading.Thread(target=work, daemon=True).start()

    def _sub_row(self):
        clear(self.sub)
        res = self.res
        if self.mode == "champion":
            ch = sorted(res.champion_radars.items(), key=lambda kv: -kv[1][0])
            if not ch:
                empty_note(self.sub, "Needs 3+ games on a champion.")
                return
            if self.sel_champ not in res.champion_radars:
                self.sel_champ = ch[0][0]
            W.TabRow(self.sub, "CHAMPION", [(c, "%s (%d)" % (cname(c), n))
                                            for c, (n, _) in ch[:8]],
                     self.sel_champ, on_change=self._pick_champ,
                     style="teal", bg=PANEL, small=True).pack(anchor="w")
        elif self.mode == "role":
            rr = sorted(res.role_radars.items(), key=lambda kv: -kv[1][0])
            if len(rr) < 1:
                empty_note(self.sub, "Needs 3+ games in a role.")
                return
            if self.sel_role not in res.role_radars:
                self.sel_role = rr[1][0] if len(rr) > 1 else rr[0][0]
            W.TabRow(self.sub, "ROLE", [(r, "%s (%d)" % (ROLE_LABEL[r], n))
                                        for r, (n, _) in rr],
                     self.sel_role, on_change=self._pick_role,
                     style="teal", bg=PANEL, small=True).pack(anchor="w")
            if self.app.role:
                tk.Label(self.sub, text="The ROLE filter is on, so only that "
                         "role's games are here — set ROLE to All to compare "
                         "roles.", bg=PANEL, fg=DIM, font=F_XS).pack(
                             anchor="w")
        elif self.mode == "compare":
            f = tk.Frame(self.sub, bg=PANEL)
            f.pack(anchor="w")
            tk.Label(f, text="RIOT ID", bg=PANEL, fg=DIM, font=F_XS_B).pack(
                side="left", padx=(0, px(8)))
            self.cmp_entry = W.entry(f, width=22, font=F_SM)
            self.cmp_entry.pack(side="left")
            if self.compare:
                self.cmp_entry.insert(0, self.compare["riot_id"])
            self.cmp_entry.bind("<Return>", lambda e: self._run_compare(
                self.cmp_entry.get()))
            W.button(f, "Compare", lambda: self._run_compare(
                self.cmp_entry.get()), font=F_SM).pack(side="left",
                                                       padx=(px(6), 0))
            self.cmp_status = tk.Label(f, text="", bg=PANEL, fg=DIM,
                                       font=F_XS)
            self.cmp_status.pack(side="left", padx=(px(8), 0))
            recent = [r["riot_id"] for r in self.app.prefs.get("recent", [])
                      if r["riot_id"].lower() !=
                      self.app.player["riot_id"].lower()][:4]
            if recent:
                rf = tk.Frame(self.sub, bg=PANEL)
                rf.pack(anchor="w", pady=(px(4), 0))
                tk.Label(rf, text="RECENT", bg=PANEL, fg=DIM,
                         font=F_XS_B).pack(side="left", padx=(0, px(8)))
                for rid in recent:
                    lb = tk.Label(rf, text=rid, bg=PANEL2, fg=DIM, font=F_XS,
                                  padx=px(6), pady=px(2), cursor="hand2")
                    lb.pack(side="left", padx=px(2))
                    lb.bind("<Button-1>", lambda e, r=rid: (
                        self.cmp_entry.delete(0, "end"),
                        self.cmp_entry.insert(0, r), self._run_compare(r)))

    def _pick_champ(self, c):
        self.sel_champ = c
        self._draw()

    def _pick_role(self, r):
        self.sel_role = r
        self._draw()

    def _draw(self):
        res = self.res
        R = res.radar
        if R is None:
            return
        # compact colour key only — the chart explains itself
        ov, legend = [], "◆ you (n=%d)" % R.n_games
        if self.mode == "previous" and res.previous_radar:
            ov.append(Overlay([a.score for a in res.previous_radar.axes],
                              GHOST, "first half"))
            legend += "   - - first half (n=%d)" % res.previous_radar.n_games
        elif self.mode == "champion" and self.sel_champ in \
                res.champion_radars:
            n, sc = res.champion_radars[self.sel_champ]
            ov.append(Overlay(sc, GOOD, cname(self.sel_champ), dashed=False,
                              fill_alpha=30))
            legend += "   ■ %s (n=%d)" % (cname(self.sel_champ), n)
        elif self.mode == "role" and self.sel_role in res.role_radars:
            n, sc = res.role_radars[self.sel_role]
            ov.append(Overlay(sc, GOOD, ROLE_LABEL[self.sel_role],
                              dashed=False, fill_alpha=30))
            legend += "   ■ %s (n=%d)" % (ROLE_LABEL[self.sel_role], n)
        elif self.mode == "compare" and self.compare and \
                self.compare.get("radar"):
            cr = self.compare["radar"]
            ov.append(Overlay([a.score for a in cr.axes], GOOD,
                              self.compare["riot_id"], dashed=False,
                              fill_alpha=30, lo=[a.lo for a in cr.axes],
                              hi=[a.hi for a in cr.axes]))
            legend += "   ■ %s (n=%d, overall %.0f)" % (
                self.compare["riot_id"], cr.n_games, cr.overall)
            if not res.reference_label:
                legend += "   — pick VS Diamond/Master+ for one shared scale"
        elif self.mode == "scales":
            os_ = getattr(self, "other_scale", None) or {}
            if os_.get("missing"):
                legend += "   — no benchmark yet: pick VS Diamond or Master+"
            elif os_.get("pending"):
                legend += "   ■ scoring vs %s…" % os_["label"]
            elif os_.get("radar"):
                o = os_["radar"]
                ov.append(Overlay([a.score for a in o.axes], GOOD,
                                  os_["label"], dashed=False, fill_alpha=30))
                legend += "   ■ vs %s (overall %.0f)" % (os_["label"],
                                                         o.overall)
        self.legend.configure(text=legend)
        self.radar.set_radar(radar_axes(R), ov)

    # ------------------------------------------------------------ compare
    def _run_compare(self, riot_id, quiet=False):
        from ...riot_client import (ReviewError, default_client, get_region,
                                    parse_riot_id)
        from ...sync import QUEUES, SyncJob
        from ...pipeline import analyze_player
        from ...riot_client import clean_riot_id
        riot_id = clean_riot_id(riot_id)
        if not riot_id:
            return
        app = self.app
        status = getattr(self, "cmp_status", None)

        def say(t, col=DIM):
            if status is not None and status.winfo_exists():
                status.configure(text=t, fg=col)
        try:
            name, tag = parse_riot_id(riot_id)
            reg = get_region(app.player["region"])
        except (ValueError, ReviewError) as e:
            say(str(e), BAD)
            return
        flt = Filter(queues=QUEUES[app.queue_key], role=app.role,
                     last_n=app.last_n)
        gen = object()
        self._cmp_gen = gen

        def analyse(puuid):
            try:
                r = analyze_player(app.cache, puuid, flt, app.legendary,
                                   limit=app.count,
                                   reference=app.current_reference())
            except Exception as e:      # noqa: BLE001
                app.post(say, "analysis failed (%s)" % type(e).__name__, BAD)
                return

            def done():
                if self._cmp_gen is not gen:
                    return
                if r.radar is None:
                    say("no games for %s with these filters" % riot_id, BAD)
                    return
                self.compare = {"riot_id": riot_id, "puuid": puuid,
                                "radar": r.radar}
                say("n=%d games" % r.n_games)
                self._draw()
            app.post(done)

        cached = app.cache.find_player(name, tag, reg.code)
        if quiet:
            if cached:
                threading.Thread(target=analyse, args=(cached,),
                                 daemon=True).start()
            return
        if self.mode != "compare":
            self.mode = "compare"
            self.modes.select("compare", fire=False)
        say("fetching %s…" % riot_id)
        try:
            client = default_client()
        except ReviewError as e:
            say(str(e), BAD)
            return
        if self._cmp_job:
            self._cmp_job.cancel()

        def prog(p):
            if p.phase == "games" and p.total:
                app.post(say, "fetching %s… %d/%d games" % (
                    riot_id, p.done, p.total))
        job = SyncJob(client, app.cache, riot_id, reg,
                      queues=QUEUES[app.queue_key],
                      count=min(app.count, 100), on_progress=prog)
        self._cmp_job = job
        job.start()

        def poll():
            if job is not self._cmp_job:
                return
            if job._thread and job._thread.is_alive():
                self.after(300, poll)
                return
            if job.error is not None:
                if cached:       # offline / rate-limited: use what we have
                    say("using cached games (%s)" % job.error)
                    threading.Thread(target=analyse, args=(cached,),
                                     daemon=True).start()
                else:
                    say(str(job.error), BAD)
                return
            if job.result and not job.result.cancelled:
                say("analysing %s…" % riot_id)
                threading.Thread(target=analyse, args=(job.result.puuid,),
                                 daemon=True).start()
        self.after(300, poll)

    # ---------------------------------------------------------- drill-down
    def _select_axis(self, axis):
        self.axis = axis
        self.radar.selected = axis
        self.radar.render()
        if getattr(self, "res", None) is not None:
            self._drill()

    def _drill(self):
        res = self.res
        clear(self.dd)
        a = res.radar.axis(self.axis)
        W.section_title(self.dd, a.label.upper() + " — DRILL-DOWN", EDGE_HI,
                        right="n=%d games" % a.n)
        head = tk.Frame(self.dd, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="%.0f" % a.score, bg=PANEL,
                 fg=score_color(a.score), font=("Georgia", 26, "bold")).pack(
                     side="left")
        ci = "90%% CI %.0f–%.0f" % (a.lo, a.hi) if a.lo == a.lo else ""
        info = tk.Frame(head, bg=PANEL)
        info.pack(side="left", padx=(px(10), 0))
        tk.Label(info, text=ci, bg=PANEL, fg=FG, font=F_SM).pack(anchor="w")
        tk.Label(info, text=fifty_means(res, res.dist_role), bg=PANEL,
                 fg=DIM, font=F_XS).pack(
                anchor="w")
        # sub-metrics
        tk.Label(self.dd, text="SUB-METRICS (PERCENTILE VS %s)" % (
            group_name(res).upper()), bg=PANEL,
                 fg=DIM, font=F_XS_B).pack(anchor="w", pady=(px(10), px(2)))
        cols = [Col("m", "METRIC", 16, "w", stretch=True),
                Col("you", "YOU", 6, "e"), Col("peer", group_name(res).upper(), 7, "e"),
                Col("pct", "PCTL", 4, "e"),
                Col("bar", "", 7, kind="bars")]
        t = Table(self.dd, cols, rows_visible=max(len(a.subs), 1),
                  tooltip=self.tip)
        rows = []
        for s in a.subs:
            ok = s.score == s.score
            rows.append(Row({
                "m": s.label, "you": s.value_text, "peer": (s.peer_text, DIM),
                "pct": ("%.0f" % s.score if ok else "—",
                        score_color(s.score) if ok else DIM),
                "bar": [(s.score / 100, score_color(s.score))] if ok
                else []}, data=s, tip=HELP.get(getattr(s, "key", ""))))
        if a.axis == "consistency":
            rows.append(Row({"m": "Game-to-game steadiness + after-loss "
                             "vs after-win", "pct": ("%.0f" % a.score,
                                                     score_color(a.score))}))
        t.set_rows(rows)
        t.pack(fill="x")
        # over time
        tk.Label(self.dd, text="OVER TIME (ROLLING 10 GAMES)", bg=PANEL,
                 fg=DIM, font=F_XS_B).pack(anchor="w", pady=(px(10), 0))
        ch = LineChart(self.dd, self.tip, height=170)
        ch.pack(fill="x")
        ser = []
        win = 10
        if a.axis in res.axis_series:
            ys = res.axis_series[a.axis]
            rm = rolling_mean(ys, min(win, len(ys)))
            for (s, col) in zip(a.subs, SUB_COLORS):
                if s.key in res.sub_series:
                    sm = rolling_mean(res.sub_series[s.key],
                                      min(win, len(ys)))
                    ser.append(Series([i + 1 for i, *_ in sm],
                                      [m for _, m, _, _ in sm], col,
                                      s.label, width=1.1,
                                      ns=[n for *_, n in sm],
                                      fmt=lambda v: "%.0f" % v))
            ser.append(Series([i + 1 for i, *_ in rm],
                              [m for _, m, _, _ in rm], EDGE_HI, a.label,
                              width=2.4, ns=[n for *_, n in rm],
                              fmt=lambda v: "%.0f" % v))
        else:
            ss = res.score_series
            ser.append(Series([i + 1 for i, *_ in ss],
                              [m for _, m, _, _ in ss], EDGE_HI,
                              "game score", width=2.2,
                              fmt=lambda v: "%.0f" % v))
        ch.set(xlabel="game (oldest → newest)",
               ylabel="percentile vs %s, 10-game average" % group_name(res),
               series=ser, ylim=(0, 100), baseline=50.0,
               xfmt=lambda v: "%d" % v, yfmt=lambda v: "%.0f" % v)
        ch.set_meta(a.label.upper() + " OVER TIME", "n=%d" % a.n, "")
        if len(ser) > 1:
            lg = tk.Frame(self.dd, bg=PANEL)
            lg.pack(fill="x")
            for s in ser:
                tk.Label(lg, text="▬ " + s.name, bg=PANEL, fg=s.color,
                         font=F_XS).pack(side="left", padx=(0, px(8)))
        # best / worst games
        if a.axis in SCORED and res.game_axes:
            j = SCORED.index(a.axis)
            by_id = {g.match_id: g for g in res.games}
            scored = sorted(((v[j], mid) for mid, v in res.game_axes.items()
                             if v[j] == v[j] and mid in by_id))
            tk.Label(self.dd, text="BEST AND WORST GAMES FOR THIS AXIS",
                     bg=PANEL, fg=DIM, font=F_XS_B).pack(
                         anchor="w", pady=(px(10), px(2)))
            gl = tk.Frame(self.dd, bg=PANEL)
            gl.pack(fill="x")
            picks = scored[::-1][:3] + scored[:3] if len(scored) >= 6 \
                else scored[::-1]
            for k, (v, mid) in enumerate(picks):
                g = by_id[mid]
                self._game_line(gl, g, v, best=k < 3)
        from ...analysis.findings import _rename_peers
        tip = _rename_peers(axis_tip(a), ("%s players" % res.reference_label)
                            if res.reference_label else None)
        if tip:
            tk.Frame(self.dd, bg=EDGE_HI, height=1).pack(
                fill="x", pady=(px(10), px(6)))
            lab = tk.Label(self.dd, text="→ " + tip, bg=PANEL,
                           fg=GOOD if a.score >= 50 else BAD, font=F_SM,
                           justify="left", anchor="w", wraplength=px(390))
            lab.pack(fill="x")
            fit_wrap(lab)

    def _game_line(self, parent, g, v, best):
        r = tk.Frame(parent, bg=PANEL2, cursor="hand2")
        r.pack(fill="x", pady=1)
        tk.Frame(r, bg=GOOD if best else BAD, width=px(3)).pack(
            side="left", fill="y")
        ic = self.app.icons.get(g.champion, px(18), lambda: None)
        if ic is not None:
            self._imgs.append(ic)
            tk.Label(r, image=ic, bg=PANEL2).pack(side="left",
                                                  padx=(px(4), 0))
        txt = "%s  %s  %d/%d/%d  ·  %s" % (
            cname(g.champion), "W" if g.win else "L", g.k, g.d, g.a,
            date_text(g.creation_ms))
        lab = tk.Label(r, text=txt, bg=PANEL2, fg=FG, font=F_XS, anchor="w")
        lab.pack(side="left", padx=px(6), pady=px(2))
        sc = tk.Label(r, text="%.0f" % v, bg=PANEL2, fg=score_color(v),
                      font=F_SM_B, width=4)
        sc.pack(side="right")
        for w in (r, lab, sc):
            w.bind("<Button-1>", lambda e, m=g.match_id: self.app.open_game(m))
            w.bind("<Enter>", lambda e: [x.configure(bg=HOVER) for x in
                                         (r, lab, sc)])
            w.bind("<Leave>", lambda e: [x.configure(bg=PANEL2) for x in
                                         (r, lab, sc)])

    # ------------------------------------------------------ small multiples
    def _multiples(self):
        res = self.res
        clear(self.sm)
        self._sm_imgs = []
        cells = [("role", r, ROLE_LABEL[r], n, sc)
                 for r, (n, sc) in sorted(res.role_radars.items(),
                                          key=lambda kv: -kv[1][0])]
        cells += [("champion", c, cname(c), n, sc)
                  for c, (n, sc) in sorted(res.champion_radars.items(),
                                           key=lambda kv: -kv[1][0])[:14]]
        nr, nc = len(res.role_radars), min(len(res.champion_radars), 14)
        self.sm_n.configure(text="%d role%s · %d champion%s (3+ games) · "
                            "click to overlay" % (nr, "" if nr == 1 else "s",
                                                  nc, "" if nc == 1 else "s"))
        size = px(92)
        per_row = 8
        for i, (kind, key, label, n, sc) in enumerate(cells):
            img = thumbnail(sc, size)
            self._sm_imgs.append(img)
            f = tk.Frame(self.sm, bg=PANEL2, padx=px(6), pady=px(4),
                         cursor="hand2")
            f.grid(row=i // per_row, column=i % per_row, padx=px(3),
                   pady=px(3), sticky="nsew")
            im = tk.Label(f, image=img, bg=PANEL2)
            im.pack()
            ok = [v for v in sc if v == v]
            avg = sum(ok) / len(ok) if ok else float("nan")
            t1 = tk.Label(f, text=label, bg=PANEL2,
                          fg=EDGE_HI if kind == "role" else FG,
                          font=F_XS_B)
            t1.pack()
            t2 = tk.Label(f, text="n=%d · avg %.0f" % (n, avg), bg=PANEL2,
                          fg=DIM, font=F_XS)
            t2.pack()
            for w in (f, im, t1, t2):
                w.bind("<Button-1>", lambda e, k=kind, key=key:
                       self._overlay_from_multiple(k, key))
        for c in range(per_row):
            self.sm.grid_columnconfigure(c, weight=1, uniform="sm")

    def _overlay_from_multiple(self, kind, key):
        if kind == "role":
            self.sel_role = key
        else:
            self.sel_champ = key
        self.mode = kind
        self.modes.select(kind, fire=False)
        self._sub_row()
        self._draw()
        self.area.to_top()
