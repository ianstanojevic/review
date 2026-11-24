"""Compare: you against players you look up — friends, duo partners,
someone from your last game. Everyone is scored on ONE shared scale (the
benchmark picked under VS, or the highest one that exists), with the same
queue / role / range filters, so the numbers compare directly.

Shows: the players (add by Riot ID, remove with ×), all radars on top of
each other, the standings on overall score with where you place, and a
head-to-head table stat by stat with the best and worst in the group
marked.
"""
from __future__ import annotations

import threading
import tkinter as tk

from ...analysis import AXES, Filter, fmt_value
from ...analysis.metrics import HELP
from ...analysis.radar import AXIS_HELP, AXIS_LABEL
from ...theme import widgets as W
from ...theme.radar import Overlay, RadarChart
from ...theme.tokens import (BAD, DIM, DMG_AD, DMG_AP, EDGE, EDGE_HI, F_LG_B,
                             F_SM, F_SM_B, F_XS, F_XS_B, FG, GOOD, PANEL,
                             PANEL2, score_color)
from ...theme.widgets import Col, Row, Table, px
from .base import (Screen, clear, empty_note, fit_wrap, grid, panel,
                   radar_axes)

MAX_OTHERS = 5
COLORS = [GOOD, DMG_AP, "#c77dff", DMG_AD, "#e8c547"]
STATS = ("kda", "cs_per_min", "gold_diff_15", "kp", "dmg_share",
         "vision_per_min", "deaths_pre14", "first_legendary_min")


def shared_scale(app):
    """(reference, label, forced): the scale everyone is scored on."""
    from ...benchmark import LEVELS, MIN_USABLE
    ref = app.current_reference()
    if ref:
        return ref, LEVELS[ref[1]].label, False
    region = app.player["region"]
    for lv in reversed(list(LEVELS)):                # highest first
        if app.cache.benchmark_info(region, lv)[0] >= MIN_USABLE:
            return (region, lv), LEVELS[lv].label, True
    return None, None, False


class CompareScreen(Screen):
    def build(self):
        self.others: list[dict] = []     # {riot_id, puuid, res, status, ...}
        self.me = None                   # my AnalysisResult on the scale
        self._gen = 0
        self._imgs = []
        from ...riot_client import clean_riot_id
        for rid in [clean_riot_id(r) for r in
                    self.app.prefs.get("compare", [])][:MAX_OTHERS]:
            self.others.append({"riot_id": rid, "res": None,
                                "status": "waiting"})
        top, _ = panel(self.body, "COMPARE", EDGE_HI, expand=False,
                       right="add players to see where you stand")
        row_ = tk.Frame(top, bg=PANEL)
        row_.pack(fill="x")
        tk.Label(row_, text="RIOT ID", bg=PANEL, fg=DIM, font=F_XS_B).pack(
            side="left", padx=(0, px(8)))
        self.entry = W.entry(row_, width=26, font=F_SM)
        self.entry.pack(side="left", ipady=3)
        self.entry.bind("<Return>", lambda e: self.add(self.entry.get()))
        W.button(row_, "Add", lambda: self.add(self.entry.get()),
                 gold=True).pack(side="left", padx=(px(6), 0))
        self.msg = tk.Label(row_, text="", bg=PANEL, fg=DIM, font=F_XS)
        self.msg.pack(side="left", padx=(px(10), 0))
        self.suggest = tk.Frame(top, bg=PANEL)
        self.suggest.pack(fill="x", pady=(px(6), 0))
        self.chips = tk.Frame(top, bg=PANEL)
        self.chips.pack(fill="x", pady=(px(8), 0))
        self.scale_note = tk.Label(top, text="", bg=PANEL, fg=DIM,
                                   font=F_XS, anchor="w", justify="left")
        self.scale_note.pack(fill="x", pady=(px(6), 0))
        self.box = tk.Frame(self.body, bg=self.body["bg"])
        self.box.pack(fill="both", expand=True)

    # ------------------------------------------------------------ data
    def update_result(self, res):
        """New filters / scale / player: re-score everyone."""
        self._reanalyse()

    def snapshot_action(self, extra):
        k, _, v = extra.partition("=")
        if k == "add":
            for rid in v.split(","):
                self.add(rid)

    def _filter(self):
        from ...sync import QUEUES
        app = self.app
        # the same filters for everyone, except champion (a friend's
        # games on YOUR champion would be a different question)
        return Filter(queues=QUEUES[app.queue_key], role=app.role,
                      last_n=app.last_n)

    def _analyse(self, puuid):
        from ...pipeline import analyze_player
        app = self.app
        ref, _, _ = shared_scale(app)
        return analyze_player(app.cache, puuid, self._filter(),
                              app.legendary, limit=app.count, reference=ref)

    def _reanalyse(self):
        self._gen += 1
        gen = self._gen
        app = self.app
        me_puuid = app.player["puuid"]
        others = [o for o in self.others]
        for o in others:
            if o.get("puuid") or o["status"] == "waiting":
                o["status"] = "scoring…" if o.get("puuid") else o["status"]
        self._render()

        def work():
            try:
                me = self._analyse(me_puuid)
            except Exception as e:           # noqa: BLE001
                app._log(e)
                me = None
            results = {}
            for o in others:
                pu = o.get("puuid") or self._cached_puuid(o["riot_id"])
                if not pu:
                    continue
                try:
                    results[o["riot_id"]] = (pu, self._analyse(pu))
                except Exception as e:       # noqa: BLE001
                    app._log(e)

            def done():
                if gen != self._gen:
                    return
                self.me = me
                for o in self.others:
                    pu, r = results.get(o["riot_id"], (None, None))
                    if pu and not o.get("region"):
                        o["region"] = app.cache.player_region(pu)
                    if r is not None and r.radar is not None:
                        o["puuid"], o["res"] = pu, r
                        o["status"] = "ready"
                    elif o["status"] == "waiting":
                        # not cached yet, or cached with no games (e.g.
                        # fetched on the wrong server before): download
                        self._fetch(o)
                    elif r is not None:
                        o["puuid"], o["res"] = pu, r
                        o["status"] = "no games with these filters"
                self._render()
            app.post(done)
        threading.Thread(target=work, daemon=True).start()

    def _cached_puuid(self, riot_id):
        from ...riot_client import parse_riot_id
        try:
            name, tag = parse_riot_id(riot_id)
        except ValueError:
            return None
        # any server: a friend may play on NA while you're on EUW
        return self.app.cache.find_player(name, tag, None)

    # ------------------------------------------------------- add/remove
    def add(self, riot_id):
        from ...riot_client import ReviewError, clean_riot_id, parse_riot_id
        riot_id = clean_riot_id(riot_id)
        if not riot_id:
            return
        try:
            parse_riot_id(riot_id)
        except (ValueError, ReviewError):
            self.msg.configure(text="write it as Name#TAG", fg=BAD)
            return
        me = self.app.player["riot_id"].lower()
        if riot_id.lower() == me:
            self.msg.configure(text="that's you", fg=DIM)
            return
        if any(o["riot_id"].lower() == riot_id.lower() for o in self.others):
            self.msg.configure(text="already added", fg=DIM)
            return
        if len(self.others) >= MAX_OTHERS:
            self.msg.configure(text="max %d players — remove one first"
                               % MAX_OTHERS, fg=BAD)
            return
        self.entry.delete(0, "end")
        self.msg.configure(text="")
        o = {"riot_id": riot_id, "res": None, "status": "fetching…"}
        self.others.append(o)
        self._save()
        self._fetch(o)
        self._render()

    def remove(self, o):
        job = o.get("job")
        if job:
            job.cancel()
        self.others = [x for x in self.others if x is not o]
        self._save()
        self._render()

    def _save(self):
        self.app.prefs.set("compare", [o["riot_id"] for o in self.others])

    def _fetch(self, o):
        """Download (or update) a player's games, then score them."""
        from ...riot_client import (PlayerNotFound, ReviewError,
                                    default_client, get_region)
        from ...sync import QUEUES, SyncJob
        app = self.app
        try:
            client = default_client()
            reg = get_region(app.player["region"])
        except (ValueError, ReviewError) as e:
            o["status"] = str(e)
            return
        o["status"] = "looking up…"

        def prog(p):
            if p.phase == "games" and p.total:
                o["status"] = "fetching %d/%d" % (p.done, p.total)
                app.post(self._chips)
        job = SyncJob(client, app.cache, o["riot_id"], reg,
                      queues=QUEUES[app.queue_key],
                      count=min(app.count, 100), on_progress=prog,
                      detect_region=True)
        o["job"] = job
        job.start()

        def poll():
            if o not in self.others:
                return
            if job._thread and job._thread.is_alive():
                self.after(400, poll)
                return
            o["job"] = None
            o["region"] = job.region.code
            if job.riot_id and job.riot_id.lower() == o["riot_id"].lower()                     and job.riot_id != o["riot_id"]:
                o["riot_id"] = job.riot_id      # Riot's own capitalisation
                self._save()
            if isinstance(job.error, PlayerNotFound):
                o["status"] = "not found"
                self._chips()
                return
            pu = (job.result.puuid if job.result else None) or \
                self._cached_puuid(o["riot_id"])
            if not pu:
                o["status"] = str(job.error or "failed")
                self._chips()
                return
            o["puuid"] = pu
            o["status"] = "scoring…"
            self._chips()

            def work():
                try:
                    r = self._analyse(pu)
                except Exception as e:       # noqa: BLE001
                    app._log(e)
                    r = None

                def done():
                    if o not in self.others:
                        return
                    o["res"] = r
                    o["status"] = "ready" if r and r.radar else \
                        "no games with these filters"
                    self._render()
                app.post(done)
            threading.Thread(target=work, daemon=True).start()
        self.after(400, poll)

    # ------------------------------------------------------------ view
    def _players(self):
        """[(name, colour, result)] — me first, then ready players."""
        out = []
        if self.me is not None and self.me.radar is not None:
            out.append((self.app.player["riot_id"] + " (you)", EDGE_HI,
                        self.me))
        for o, col in zip(self.others, COLORS):
            r = o.get("res")
            if r is not None and r.radar is not None:
                out.append((o["riot_id"], col, r))
        return out

    def _rank(self, res):
        from ..app import rank_text
        q = "RANKED_FLEX_SR" if self.app.queue_key == "flex" \
            else "RANKED_SOLO_5x5"
        h = self.app.cache.rank_history(res.puuid, q)
        return rank_text(h[-1] if h else None)

    def _chips(self):
        if not self.chips.winfo_exists():
            return
        clear(self.chips)
        self._imgs = []
        mine = [(self.app.player["riot_id"], EDGE_HI, None, "you")]
        rows = mine + [(o["riot_id"], col, o, o["status"])
                       for o, col in zip(self.others, COLORS)]
        for rid, col, o, status in rows:
            c = tk.Frame(self.chips, bg=PANEL2, padx=px(8), pady=px(4))
            c.pack(side="left", padx=(0, px(6)), pady=px(2))
            tk.Frame(c, bg=col, width=px(10), height=px(10)).pack(
                side="left", padx=(0, px(6)))
            tk.Label(c, text=rid, bg=PANEL2, fg=FG, font=F_SM_B).pack(
                side="left")
            reg = (o or {}).get("region")
            if reg and reg != self.app.player["region"]:
                tk.Label(c, text=reg, bg=PANEL2, fg=DIM, font=F_XS_B).pack(
                    side="left", padx=(px(6), 0))
            res = (o or {}).get("res") if o else self.me
            sub = status
            few = False
            if res is not None and status in ("ready", "you"):
                sub = "%s · %d games" % (self._rank(res), res.n_games)
                if res.radar is not None and res.radar.insufficient:
                    few = True
                    sub = "%s · only %d games — rough" % (
                        self._rank(res), res.n_games)
            tk.Label(c, text=sub, bg=PANEL2,
                     fg=BAD if status.startswith(("not found", "no games",
                                                  "Riot")) else
                     EDGE_HI if few else DIM,
                     font=F_XS).pack(side="left", padx=(px(8), 0))
            if o is not None and o.get("puuid"):
                # click a friend's card: open their whole analysis
                def open_full(e, o=o):
                    self.app.view = "overview"
                    self.app.nav.select("overview", fire=False)
                    self.app.fetch(o["riot_id"], o.get("region") or
                                   self.app.player["region"])
                for w in c.winfo_children():
                    w.bind("<Button-1>", open_full)
                    w.configure(cursor="hand2")
                c.bind("<Button-1>", open_full)
                c.configure(cursor="hand2")
                c.bind("<Enter>", lambda e, n=o["riot_id"]: self.tip.show(
                    e.x_root, e.y_root, "Open %s's full analysis" % n))
                c.bind("<Leave>", lambda e: self.tip.hide())
            if o is not None:
                x = tk.Label(c, text="×", bg=PANEL2, fg=DIM, font=F_SM_B,
                             cursor="hand2")
                x.pack(side="left", padx=(px(8), 0))
                x.bind("<Button-1>", lambda e, o=o: self.remove(o))
                x.bind("<Enter>", lambda e, x=x: x.configure(fg=BAD))
                x.bind("<Leave>", lambda e, x=x: x.configure(fg=DIM))
        # quick-add: recent searches not in the list yet
        clear(self.suggest)
        have = {o["riot_id"].lower() for o in self.others} | {
            self.app.player["riot_id"].lower()}
        recent = [r["riot_id"] for r in self.app.prefs.get("recent", [])
                  if r["riot_id"].lower() not in have][:5]
        if recent and len(self.others) < MAX_OTHERS:
            tk.Label(self.suggest, text="RECENT", bg=PANEL, fg=DIM,
                     font=F_XS_B).pack(side="left", padx=(0, px(8)))
            for rid in recent:
                b = tk.Label(self.suggest, text="+ " + rid, bg=PANEL2,
                             fg=DIM, font=F_XS, padx=px(6), pady=px(2),
                             cursor="hand2")
                b.pack(side="left", padx=(0, px(4)))
                b.bind("<Button-1>", lambda e, r=rid: self.add(r))

    def _render(self):
        if not self.box.winfo_exists():
            return
        self._chips()
        ref, label, forced = shared_scale(self.app)
        if label:
            self.scale_note.configure(
                text="Everyone is scored against %s players in their role%s"
                " — so the numbers compare directly." % (
                    label, " (picked automatically: VS My games would "
                    "score each player against their own lobbies)"
                    if forced else ""), fg=DIM)
        else:
            self.scale_note.configure(
                text="No rank benchmark yet: each player is scored against "
                "the players in their own games, so a Gold and a Master "
                "player can both show 50. Pick VS Gold…Master+ (top right) "
                "once to put everyone on one scale.", fg=BAD)
        clear(self.box)
        players = self._players()
        if len(players) < 2:
            p, _ = panel(self.box, None, expand=False, pady=(px(8), 0))
            empty_note(p, "Add a friend's Riot ID above — or pick one of "
                       "your recent searches — to see how you stack up: "
                       "radar on radar, standings, and stat by stat.")
            return
        left, right = grid(self.box, 2, pady=(px(8), 0))
        self._radar(left, players)
        self._standings(right, players)
        # up to 3 players the stat table fits beside the radar, filling the
        # space under the standings; more columns need the full width
        if len(players) <= 3:
            self._table(players, right)
        else:
            self._table(players, self.box)

    def _radar(self, parent, players):
        p, _ = panel(parent, "RADARS", EDGE_HI, expand=False,
                     right="%d players · same scale" % len(players))
        me = players[0][2]
        rc = RadarChart(p, self.tip, height=430)
        rc.pack(fill="x")
        ov = [Overlay([a.score for a in r.radar.axes], col, name,
                      dashed=False, fill_alpha=16)
              for name, col, r in players[1:]]
        rc.set_radar(radar_axes(me.radar), ov, animate=False)
        key = tk.Frame(p, bg=PANEL)
        key.pack(fill="x")
        for name, col, r in players:
            tk.Label(key, text="■ %s %.0f" % (name.split("#")[0],
                                              r.radar.overall),
                     bg=PANEL, fg=col, font=F_XS).pack(side="left",
                                                       padx=(0, px(10)))

    def _standings(self, parent, players):
        p, _ = panel(parent, "STANDINGS", EDGE_HI, expand=False,
                     right="overall score · 90% range")
        order = sorted(players, key=lambda t: -t[2].radar.overall)
        my_place = next(i for i, t in enumerate(order) if t is players[0])
        top = max(t[2].radar.overall for t in order)
        lo = min(t[2].radar.overall_lo for t in order
                 if t[2].radar.overall_lo == t[2].radar.overall_lo)
        lo = min(lo, 40)
        for i, (name, col, r) in enumerate(order):
            R = r.radar
            row_ = tk.Frame(p, bg=PANEL2 if t_is_me(order[i], players)
                            else PANEL, padx=px(6), pady=px(4))
            row_.pack(fill="x", pady=1)
            bg = row_["bg"]
            tk.Label(row_, text="%d" % (i + 1), bg=bg, fg=DIM, font=F_LG_B,
                     width=2).pack(side="left")
            tk.Frame(row_, bg=col, width=px(4), height=px(26)).pack(
                side="left", padx=(px(4), px(8)))
            tk.Label(row_, text=name, bg=bg, fg=EDGE_HI if t_is_me(
                order[i], players) else FG, font=F_SM_B, width=24,
                anchor="w").pack(side="left")
            bar = tk.Canvas(row_, bg=bg, highlightthickness=0, height=px(12),
                            width=1)
            bar.pack(side="left", fill="x", expand=True, padx=px(8))
            half = (R.overall_hi - R.overall_lo) / 2 \
                if R.overall_lo == R.overall_lo else 0

            def draw(e, b=bar, v=R.overall, h=half, c=col):
                b.delete("all")
                span = max(top + 5 - lo, 1)
                X = lambda s: (s - lo) / span * e.width
                b.create_rectangle(0, 2, X(v), e.height - 2, fill=c, width=0)
                b.create_line(X(v - h), e.height / 2, X(v + h), e.height / 2,
                              fill=FG)
                x50 = X(50)
                b.create_line(x50, 0, x50, e.height, fill=EDGE, dash=(2, 2))
            bar.bind("<Configure>", draw)
            tk.Label(row_, text="%.0f ±%.0f" % (R.overall, half), bg=bg,
                     fg=score_color(R.overall), font=F_SM_B, width=7,
                     anchor="e").pack(side="left")
        # where you lead and trail the group
        me = players[0][2].radar
        lead, trail = [], []
        for j, ax in enumerate(me.axes):
            # compare the numbers as shown (rounded), like the table does
            others = [round(t[2].radar.axes[j].score) for t in players[1:]]
            mine = round(ax.score)
            if mine > max(others):
                lead.append(AXIS_LABEL[ax.axis])
            elif mine < min(others):
                trail.append(AXIS_LABEL[ax.axis])
        n = len(players)
        place = ["1st", "2nd", "3rd", "4th", "5th", "6th"][my_place]
        lines = ["You're %s of %d on overall score." % (place, n)]
        if lead:
            lines.append("Best in the group at: %s." % ", ".join(lead))
        if trail:
            lines.append("Lowest in the group at: %s." % ", ".join(trail))
        summary = tk.Label(p, text="  ".join(lines), bg=PANEL, fg=FG,
                           font=F_SM, anchor="w", justify="left",
                           wraplength=px(520))
        summary.pack(fill="x", pady=(px(8), 0))
        fit_wrap(summary)

    def _table(self, players, parent):
        p, _ = panel(parent, "HEAD TO HEAD", EDGE_HI, expand=False,
                     pady=(px(8), 0),
                     right="teal = best in the group · red = lowest")
        cols = [Col("stat", "", 18, "w", stretch=True)] + [
            Col("p%d" % i, name.split("#")[0] + (
                "" if i else " (you)"), 12, "e", raw_title=True)
            for i, (name, _, _) in enumerate(players)]
        rows = []

        def add(label, vals, texts, higher_better=True, tip=None):
            cells = {"stat": label}
            ok = [v for v in vals if v is not None and v == v]
            best = worst = None
            if len(ok) >= 2 and max(ok) != min(ok):
                best = max(ok) if higher_better else min(ok)
                worst = min(ok) if higher_better else max(ok)
            shown = {t for v, t in zip(vals, texts) if v is not None and
                     v == v}
            if len(shown) < 2:              # equal as displayed: no winner
                best = worst = None
            for i, (v, t) in enumerate(zip(vals, texts)):
                col = FG
                if v is not None and v == v:
                    if v == best:
                        col = GOOD
                    elif v == worst:
                        col = BAD
                cells["p%d" % i] = (t, col)
            rows.append(Row(cells, marked=label == "Overall score",
                            tip=tip))

        R = [t[2] for t in players]
        add("Overall score", [r.radar.overall for r in R],
            ["%.0f ±%.0f" % (r.radar.overall, (r.radar.overall_hi -
                                                 r.radar.overall_lo) / 2)
             if r.radar.overall_lo == r.radar.overall_lo else
             "%.0f" % r.radar.overall for r in R])
        for j, ax in enumerate(AXES):
            add(AXIS_LABEL[ax], [r.radar.axes[j].score for r in R],
                ["%.0f" % r.radar.axes[j].score for r in R],
                tip=AXIS_HELP.get(ax))
        add("Win rate", [r.record.p for r in R],
            ["%.0f%% ±%.0f" % (100 * r.record.p, 100 * (r.record.ci[1] -
                                                         r.record.ci[0]) / 2)
             for r in R])
        from ...ui.catalogue import ladder
        ranks = []
        for r in R:
            q = "RANKED_FLEX_SR" if self.app.queue_key == "flex" \
                else "RANKED_SOLO_5x5"
            h = self.app.cache.rank_history(r.puuid, q)
            ranks.append(ladder(h[-1]) if h else float("nan"))
        add("Rank", ranks, [self._rank(r) for r in R])
        add("Games", [None] * len(R), [str(r.n_games) for r in R])
        from ...analysis import ROLE_LABEL
        add("Main role", [None] * len(R),
            [ROLE_LABEL.get(r.dist_role, "—") for r in R])
        for key in STATS:
            vals, texts, label, pol = [], [], key, 1
            for r in R:
                ks = next((k for k in r.key_stats if k[0] == key), None)
                if ks is None:
                    vals.append(None)
                    texts.append("—")
                    continue
                _, label, m, _, pol = ks
                vals.append(m.mean)
                texts.append(fmt_value(key, m.mean))
            add(label, vals, texts, higher_better=pol >= 0,
                tip=HELP.get(key))
        # each player column as wide as its longest text ("Challenger 3662
        # LP" overflowed a fixed 12-character column into its neighbour);
        # measured in pixels, since Col.width counts "0"-widths
        import tkinter.font as tkfont
        from ...theme.tokens import F_SM, F_XS_B
        f_cell, f_head = tkfont.Font(font=F_SM), tkfont.Font(font=F_XS_B)
        zero = max(f_cell.measure("0"), 1)
        for c in cols[1:]:
            texts = [r.cells[c.key][0] if isinstance(r.cells.get(c.key),
                                                     tuple)
                     else str(r.cells.get(c.key, "")) for r in rows]
            px_w = max([f_cell.measure(t) for t in texts] +
                       [f_head.measure(c.title)])
            c.width = max(c.width, -(-px_w // zero) + 1)
        t = Table(p, cols, rows_visible=len(rows), tooltip=self.tip)
        t.set_rows(rows)
        t.pack(fill="x")


def t_is_me(t, players):
    return t is players[0]
