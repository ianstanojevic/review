"""The chart catalogue: one builder per chart, shared by the topic screens
and the Stats lab. Every builder takes (parent, screen, result), creates a
captioned chart block (title · n · takeaway, PNG export on right-click) and
fills it. Nothing here shows a bare average without its n.
"""
from __future__ import annotations

import math
import tkinter as tk
from collections import defaultdict

from ..analysis import ROLE_LABEL, fmt_value
from ..analysis.stats import Z90, Rate, mean_se
from ..theme import widgets as W
from ..theme.charts import (BarChart, BinBar, Distribution, Heatmap,
                            LineChart, MapChart, Point, Scatter, Series,
                            fmt_pct)
from ..theme.tokens import (BAD, DIM, EDGE_HI, F_XS, FG, GHOST, GOOD, PANEL,
                            score_color, signed_color)
from ..theme.widgets import px
from . import takeaways as T
from .screens.base import (avg_word, cname, chart_block, empty_note,
                           fifty_means,
                           mean_pm, pm_text, rate_bins, wr_text)

UNIT = {"gold": "gold", "cs": "CS", "xp": "XP"}
WR_X = {"gd15": "gold vs lane opponent at 15:00",
        "cs10": "CS vs lane opponent at 10:00",
        "early_deaths": "deaths before 14:00",
        "length": "game length (minutes)"}
MAP_MAX = 14870.0
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
         "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIVS = {"IV": 0, "III": 1, "II": 2, "I": 3}


def chron(res):
    """Games oldest → newest (res.games is newest first)."""
    return res.games[::-1]


def patch_regions(res, offset=1):
    """[(x_start, x_end, patch)] spans over chronological game numbers."""
    out, games = [], chron(res)
    i = 0
    while i < len(games):
        j = i
        while j + 1 < len(games) and games[j + 1].patch == games[i].patch:
            j += 1
        out.append((i + offset, j + offset, games[i].patch))
        i = j + 1
    return out


# ------------------------------------------------------------- lane

def lane_curve(parent, s, res, metric, height=190):
    c = res.lane_curves[metric]
    unit = UNIT[metric]
    ch = chart_block(parent, LineChart, "%s DIFF VS LANE OPPONENT" %
                     unit.upper(), height=height, tip=s.tip)
    f = (lambda v: "%+.1f" % v) if metric == "cs" else (
        lambda v: "%+.0f" % v)
    ser = []
    if c["all"]:
        a = c["all"]
        ser.append(Series([p.minute for p in a], [p.mean for p in a], GHOST,
                          "all games", ns=[p.n for p in a], width=1.2,
                          dash=(4, 3), fmt=f))
    for split, col, name in (("win", GOOD, "wins"), ("loss", BAD, "losses")):
        pts = c[split]
        if pts:
            ser.append(Series([p.minute for p in pts], [p.mean for p in pts],
                              col, name, lo=[p.lo for p in pts],
                              hi=[p.hi for p in pts], ns=[p.n for p in pts],
                              band_alpha=34, fmt=f))
    ch.set(series=ser, xlabel="game time", ylabel="%s ahead (+) / behind "
           "(−) your lane opponent" % unit,
           xfmt=lambda v: "%d:00" % v, yfmt=f, baseline=0.0,
           baseline_label="even", xticks=[0, 5, 10, 15, 20])
    n = max((p.n for p in c["all"]), default=0)
    ch.meta("n=%d games · 90%% CI bands" % n, T.lane_curve(res, metric))
    return ch


def wr_bars(parent, s, res, key, title, take, height=170):
    bins = res.wr_curves[key]
    ch = chart_block(parent, BarChart, title, height=height, tip=s.tip)
    ch.set(bins=rate_bins(bins), baseline=0.5, baseline_label="50%",
           ylabel="win rate", xlabel=WR_X.get(key, ""))
    ch.meta("n=%d games · Wilson 90%%" % sum(b.rate.n for b in bins),
            take(res))
    return ch


def distribution(parent, s, res, key, label, height=150):
    me, peers = res.distributions.get(key, ([], []))
    role = ROLE_LABEL.get(res.dist_role, "")
    grp = ("%s %s" % (res.reference_label, role)) if \
        res.reference_label else "%s peers" % role
    ch = chart_block(parent, Distribution, "%s — YOU VS %s" % (
        label.upper(), grp.upper()), height=height, tip=s.tip)
    ch.set(xlabel=label.lower() + " per game", ylabel="share of games",
           mine=me, peers=peers, fmt=lambda v: fmt_value(key, v),
           label_peers=grp)
    ch.meta("you n=%d · them n=%d" % (
        sum(v == v for v in me), sum(v == v for v in peers)),
        T.distribution(res, key, label.lower()))
    return ch


def matchup_table(parent, s, res, rows_visible=8):
    from ..theme.widgets import Col, Row, Table
    cols = [Col("icon", "", 0, kind="icon"),
            Col("opp", "VS", 13, "w", sort=lambda r: r.data.opponent,
                stretch=True),
            Col("n", "GAMES", 6, "e", sort=lambda r: r.data.rate.n),
            Col("wr", "WR ±", 9, "e", sort=lambda r: r.data.rate.p),
            Col("gd", "GD@15 ±", 11, "e", sort=lambda r: r.data.gd15.mean),
            Col("cs", "CS@10 ±", 10, "e", sort=lambda r: r.data.cs10.mean),
            Col("bar", "", 10, kind="bars")]
    t = Table(parent, cols, icon_loader=s.app.icons.get,
              rows_visible=rows_visible, tooltip=s.tip, default_sort="n")
    rows = []
    for m in res.matchups:
        p = m.rate.p
        rows.append(Row({
            "opp": cname(m.opponent), "n": str(m.rate.n),
            "wr": ("%s %s" % (wr_text(m.rate), pm_text(m.rate)),
                   GOOD if p > 0.5 else BAD if p < 0.5 else FG),
            "gd": (mean_pm("gold_diff_15", m.gd15),
                   signed_color(m.gd15.mean)),
            "cs": (mean_pm("cs_diff_10", m.cs10),
                   signed_color(m.cs10.mean)),
            "bar": [(p, GOOD), (1 - p, BAD)] if m.rate.n else []},
            icon=m.opponent, data=m))
    t.set_rows(rows)
    t.pack(fill="x")
    return t


# ----------------------------------------------------------- trends

def trend_chart(parent, s, res, t, height=200, title=None, take=None):
    grp = getattr(res, "reference_label", None) or "peer"
    ch = chart_block(parent, LineChart, title or "%s — YOU VS %s AVERAGE "
                     "(LAST 20 GAMES AT EACH POINT)" % (t.label.upper(),
                                                        grp.upper()),
                     height=height, tip=s.tip)
    xs = [i + 1 for i, _, _, _ in t.rolling]
    ys = [m for _, m, _, _ in t.rolling]
    lo = [m - Z90 * se for _, m, se, _ in t.rolling]
    hi = [m + Z90 * se for _, m, se, _ in t.rolling]
    f = lambda v, k=t.key: fmt_value(k, v)
    you = Series(xs, ys, GOOD, "You", lo=lo, hi=hi,
                 ns=[n for *_, n in t.rolling], fmt=f)
    ref = t.rolling_ref if getattr(t, "rolling_ref", None) else None
    if ref:
        # the group's average for the roles you played in those same 20
        # games: the gap between the lines is exactly what the verdict
        # measures, even when you switched roles along the way
        series = [Series([i + 1 for i, *_ in ref], [m for _, m, _, _ in ref],
                         EDGE_HI, "%s average" % grp, width=1.5, dash=(6, 4),
                         fmt=f), you]
        base = dict(baseline=None, baseline_label="")
        legend = "teal = you · dashed gold = %s average for the roles you "             "played · shaded = patches" % grp
    else:
        avg = (getattr(res, "peer_means", {}) or {}).get(t.key)
        series = [you]
        base = dict(baseline=avg, baseline_label=(
            "%s %s" % (avg_word(res), fmt_value(t.key, avg)))
            if avg is not None else "")
        legend = "teal = you · shaded = patches"
    ch.set(xlabel="game (oldest → newest)",
           ylabel="%s, average of 20 games" % t.label,
           series=series, regions=patch_regions(res),
           xfmt=lambda v: "%d" % v, yfmt=f, **base)
    ch.meta(legend, take if take is not None else T.trend(t, res))
    return ch


def score_trend(parent, s, res, height=190):
    ch = chart_block(parent, LineChart, "GAME SCORE (LAST 20 GAMES AT EACH POINT)",
                     height=height, tip=s.tip)
    ss = res.score_series
    f = lambda v: "%.0f" % v
    ch.set(xlabel="game (oldest → newest)",
           ylabel="game score, average of 20 games",
           series=[Series([i + 1 for i, *_ in ss], [m for _, m, _, _ in ss],
                          EDGE_HI, "score",
                          lo=[m - Z90 * se for _, m, se, _ in ss],
                          hi=[m + Z90 * se for _, m, se, _ in ss],
                          ns=[n for *_, n in ss], fmt=f)],
           regions=patch_regions(res), xfmt=lambda v: "%d" % v, yfmt=f,
           baseline=50.0, baseline_label="50 = typical %s" % (
               res.reference_label or "peer"))
    ch.meta("n=%d games · 90%% CI" % res.n_games,
            "Game score = weighted mean of the axis percentiles in each game;"
            " %s." % fifty_means(res))
    return ch


def patch_scores(parent, s, res, height=170):
    by = defaultdict(list)
    for g in chron(res):
        by[g.patch].append(g.score)
    order = []
    for g in chron(res):
        if g.patch not in order:
            order.append(g.patch)
    bins = []
    for pt in order:
        m = mean_se(by[pt])
        bins.append(BinBar(pt, m.mean, m.lo, m.hi, m.n,
                           "Patch %s: score %.0f ±%.0f, n=%d" % (
                               pt, m.mean, m.half if m.half == m.half
                               else 0, m.n)))
    ch = chart_block(parent, BarChart, "PERFORMANCE BY PATCH (GAME SCORE)",
                     height=height, tip=s.tip)
    ch.set(xlabel="patch", ylabel="average game score",
           bins=bins, baseline=50, baseline_label="50 = typical peer",
           ylim=(0, 100), yfmt=lambda v: "%.0f" % v,
           vfmt=lambda v: "%.0f" % v)
    ranked = sorted((b for b in bins if b.n >= 5), key=lambda b: -b.value)
    take = ("Best patch: %s (%.0f, n=%d); worst: %s (%.0f, n=%d)." % (
        ranked[0].label, ranked[0].value, ranked[0].n, ranked[-1].label,
        ranked[-1].value, ranked[-1].n)) if len(ranked) >= 2 else \
        "Needs 5+ games on at least two patches to rank them."
    ch.meta("n=%d games · 90%% CI" % res.n_games, take)
    return ch


def ladder(snap) -> float:
    if not snap.tier or snap.tier not in TIERS:
        return float("nan")
    t = TIERS.index(snap.tier)
    if t >= 7:
        return 7 * 400 + (snap.lp or 0)
    return t * 400 + DIVS.get(snap.division, 0) * 100 + (snap.lp or 0)


def ladder_text(v: float) -> str:
    if v != v:
        return "—"
    t = int(v // 400)
    if t >= 7:
        return "Master+ %d LP" % (v - 2800)
    div = int((v - t * 400) // 100)
    return "%s %s" % (TIERS[t].capitalize(), ["IV", "III", "II", "I"][div])


def lp_history(parent, s, res, height=170):
    q = "RANKED_FLEX_SR" if s.app.queue_key == "flex" else "RANKED_SOLO_5x5"
    hist = s.app.cache.rank_history(res.puuid, q)
    pts = [(h.taken_at, ladder(h)) for h in hist if ladder(h) == ladder(h)]
    ch = chart_block(parent, LineChart, "RANK OVER TIME (YOUR SNAPSHOTS)",
                     height=height, tip=s.tip)
    if len(pts) < 2:
        ch.set(series=[])
        ch.meta("n=%d snapshot%s" % (len(pts), "" if len(pts) == 1 else "s"),
                "Riot's API has no LP history, so REVIEW saves a rank "
                "snapshot every time you analyse this player. The line builds "
                "up over future runs.")
        return ch
    import datetime as dt
    t0 = pts[0][0]
    xs = [(t - t0) / 86400 for t, _ in pts]
    ch.set(xlabel="snapshot date", ylabel="rank",
           series=[Series(xs, [v for _, v in pts], EDGE_HI, "rank",
                          fmt=ladder_text)],
           yfmt=ladder_text,
           xfmt=lambda v: dt.datetime.fromtimestamp(t0 + v * 86400)
           .strftime("%d %b"))
    d = pts[-1][1] - pts[0][1]
    ch.meta("n=%d snapshots" % len(pts),
            "%s LP since your first snapshot (%s → %s)." % (
                "%+d" % d, ladder_text(pts[0][1]), ladder_text(pts[-1][1])))
    return ch


# --------------------------------------------------------- champions

def champion_scatter(parent, s, res, height=260, label_top=10):
    rows = [c for c in res.champions if c.games >= 2]
    pts = []
    for c in rows:
        sc = c.score.mean
        lo, hi = c.rate.ci
        pts.append(Point(c.games, c.rate.p, r=max(3.5, min(11, 3 + (
            (sc if sc == sc else 50) - 25) / 6)), lo=lo, hi=hi,
            label=cname(c.name), color=score_color(sc) if sc == sc else DIM,
            tip="%s — %d games, %s WR [%s–%s], score %.0f" % (
                cname(c.name), c.games, wr_text(c.rate), fmt_pct(lo),
                fmt_pct(hi), sc)))
    ch = chart_block(parent, Scatter, "CHAMPIONS — GAMES VS WIN RATE",
                     height=height, tip=s.tip)
    med = sorted(c.games for c in rows)[len(rows) // 2] if rows else 0
    ch.set(ylabel="win rate (bars = 90% range)",
           points=pts, ylim=(0, 1), hline=0.5, vline=med,
           quadrants=("HIDDEN GEMS", "COMFORT PICKS", "UNPROVEN",
                      "PROBLEM PICKS"), xlabel="games played",
           label_top=label_top)
    ch.meta("%d champions with 2+ games · bubble = avg score · bars = "
            "Wilson 90%%" % len(rows), T.champions(res))
    return ch


# ---------------------------------------------------------- sessions

def heatmap(parent, s, res, height=190):
    ch = chart_block(parent, Heatmap, "WIN RATE BY WEEKDAY × HOUR",
                     height=height, tip=s.tip)
    ch.set(xlabel="hour of day", ylabel="weekday",
           grid=res.sessions.heatmap,
           row_labels="Mon Tue Wed Thu Fri Sat Sun".split(),
           col_labels=["%02d" % h for h in range(24)])
    ch.meta("n=%d games · colour = WR, opacity = games" % res.n_games,
            T.heatmap(res))
    return ch


def session_bars(parent, s, res, which, height=170):
    ss = res.sessions
    if which == "number":
        bins, title, take = ss.by_game_number, \
            "WIN RATE BY GAME # IN SESSION", T.session_number(res)
    else:
        bins, title, take = ss.after_losses, \
            "WIN RATE AFTER CONSECUTIVE LOSSES", T.streaks(res)
    ch = chart_block(parent, BarChart, title, height=height, tip=s.tip)
    ch.set(bins=rate_bins(bins), baseline=0.5, baseline_label="50%",
           ylabel="win rate",
           xlabel="game number in a session" if which == "number"
           else "losses in a row before the game")
    ch.meta("n=%d games · %d sessions (gap > 60 min) · Wilson 90%%" % (
        sum(b.rate.n for b in bins), ss.n_sessions), take)
    return ch


# ------------------------------------------------------------ deaths

def death_points(res, phase=None):
    """(x, y, tip) mirrored so your base is always bottom-left."""
    out = []
    for t, x, y, ph, blue in res.deaths.points:
        if phase and ph != phase:
            continue
        if not blue:
            x, y = MAP_MAX - x, MAP_MAX - y
        out.append((x, y, "%d:%02d" % (int(t), int(t % 1 * 60))))
    return out


def death_map(parent, s, res, phase=None, height=360):
    ch = chart_block(parent, MapChart, "WHERE YOU DIE", height=height,
                     tip=s.tip)
    ch.minimap = s.app.icons.minimap()
    pts = death_points(res, phase)
    ch.set(points=pts)
    ch.meta("n=%d deaths%s · your base bottom-left" % (
        len(pts), " (%s min)" % phase if phase else ""), T.deaths(res))
    return ch


def death_minutes(parent, s, res, height=170):
    bm = res.deaths.by_minute
    total = sum(bm) or 1
    edges = list(range(0, 45, 5)) + [61]
    bins = []
    for a, b in zip(edges, edges[1:]):
        n = sum(bm[a:b])
        lab = "%d–%d" % (a, b - 1) if b <= 45 else "%d+" % a
        bins.append(BinBar(lab, n / total, n=n, tip="minute %s: %d deaths "
                           "(%.0f%% of all)" % (lab, n, 100 * n / total)))
    top = max((b.value for b in bins), default=0.2)
    ch = chart_block(parent, BarChart, "DEATHS BY MINUTE", height=height,
                     tip=s.tip)
    ch.set(xlabel="game minute of the death", ylabel="share of your deaths",
           bins=bins, baseline=0, ylim=(0, max(top * 1.25, 0.05)),
           neutral=True)
    ch.meta("n=%d deaths in %d games" % (sum(bm), res.n_games),
            T.death_minutes(res))
    return ch


# ------------------------------------------------------------- items

def item_bars(parent, s, res, height=170):
    it = res.items
    ch = chart_block(parent, BarChart, "WIN RATE BY FIRST LEGENDARY MINUTE",
                     height=height, tip=s.tip)
    ch.set(bins=rate_bins(it.bins), baseline=0.5, baseline_label="50%",
           ylabel="win rate",
           xlabel="minute your first legendary item finished")
    ch.meta("n=%d games with a legendary · Wilson 90%%" % len(it.points),
            T.items(res))
    return ch


def item_scatter(parent, s, res, height=200):
    it = res.items
    import random
    rnd = random.Random(7)
    pts = [Point(t, (0.93 if w else 0.07) + rnd.uniform(-0.05, 0.05), r=3,
                 color=GOOD if w else BAD,
                 tip="%s · first legendary at %.1f min" % (
                     "Win" if w else "Loss", t)) for t, w in it.points]
    centers = {"<12m": 11, "12–14": 13, "14–16": 15, "16–18": 17, "18m+": 19}
    curve = [(centers[b.label], b.rate.p) for b in it.bins
             if b.rate.n >= 3 and b.label in centers]
    ch = chart_block(parent, Scatter, "FIRST LEGENDARY ITEM — EVERY GAME",
                     height=height, tip=s.tip)
    avg_item = (getattr(res, "peer_means", {}) or {}).get(
        "first_legendary_min")
    ch.set(ylabel="result (gold line = win rate)",
           vline=avg_item, vline_label=("%s %.1f min" % (
               avg_word(res), avg_item)) if avg_item else "",
           points=pts, ylim=(0, 1), hline=0.5, curve=curve,
           xfmt=lambda v: "%g min" % v, yfmt=lambda v: {0: "loss", 1: "win"}
           .get(round(v, 2), fmt_pct(v)),
           xlabel="minute your first legendary item finished",
           label_top=0)
    ch.meta("n=%d games · mean %s" % (len(it.points), mean_pm(
        "first_legendary_min", it.mean)), T.items(res))
    return ch


# -------------------------------------------------------------- duos

def duo_table(parent, s, res, rows_visible=8):
    from ..theme.widgets import Col, Row, Table
    cols = [Col("name", "TEAMMATE", 18, "w", sort=lambda r: r.data.riot_id,
                stretch=True),
            Col("n", "GAMES", 6, "e", sort=lambda r: r.data.rate.n),
            Col("wr", "WR ±", 9, "e", sort=lambda r: r.data.rate.p),
            Col("sc", "YOUR SCORE ±", 12, "e",
                sort=lambda r: r.data.score.mean),
            Col("bar", "", 10, kind="bars")]
    t = Table(parent, cols, rows_visible=rows_visible, tooltip=s.tip,
              default_sort="n")
    rows = []
    for d in res.duos:
        p = d.rate.p
        sc = d.score
        rows.append(Row({
            "name": d.riot_id, "n": str(d.rate.n),
            "wr": ("%s %s" % (wr_text(d.rate), pm_text(d.rate)),
                   GOOD if p > 0.5 else BAD if p < 0.5 else FG),
            "sc": ("%.0f ±%.0f" % (sc.mean, sc.half) if sc.half == sc.half
                   else "%.0f" % sc.mean, score_color(sc.mean)),
            "bar": [(p, GOOD), (1 - p, BAD)]}, data=d))
    t.set_rows(rows)
    t.pack(fill="x")
    if not res.duos:
        empty_note(parent, "Teammates show up here once they've been in 3+ "
                   "of these games.")
    return t
