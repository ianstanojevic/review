"""Aggregates and chart data. Every number ships with its n and an interval.

All functions take ``games``: the player's analysed games in chronological
order, as (MatchFeatures, PlayerGame) pairs, plus per-game scores where
relevant. Pure functions: no I/O.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .metrics import METRICS
from .model import MatchFeatures, PlayerGame
from .stats import (Effect, Mean, Rate, Slope, arr, cohen_d, mean_se,
                    ols_slope, rolling_mean)
from .zones import RELATIVE_LABEL, relative_zone

Game = tuple[MatchFeatures, PlayerGame]


def col(games: list[Game], key: str) -> np.ndarray:
    return np.array([p.stats.get(key, np.nan) for _, p in games], float)


# ------------------------------------------------------------ group tables

@dataclass
class GroupRow:
    name: str
    games: int
    rate: Rate
    kda: float
    cs_per_min: Mean
    gd15: Mean
    dmg_share: Mean
    score: Mean
    extra: dict = field(default_factory=dict)


def group_rows(games: list[Game], scores: np.ndarray, key) -> list[GroupRow]:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, (m, p) in enumerate(games):
        groups[key(m, p)].append(i)
    rows = []
    for name, ix in groups.items():
        gs = [games[i] for i in ix]
        k = sum(p.stats["kills"] + p.stats["assists"] for _, p in gs)
        d = sum(p.stats["deaths"] for _, p in gs)
        rows.append(GroupRow(
            name=name, games=len(gs),
            rate=Rate(sum(p.win for _, p in gs), len(gs)),
            kda=k / max(d, 1), cs_per_min=mean_se(col(gs, "cs_per_min")),
            gd15=mean_se(col(gs, "gold_diff_15")),
            dmg_share=mean_se(col(gs, "dmg_share")),
            score=mean_se(scores[ix]),
            extra={"dmg_mix": tuple(
                float(np.nanmean([p.dmg_mix[k] for _, p in gs]))
                if any(p.dmg_mix[k] == p.dmg_mix[k] for _, p in gs) else 0.0
                for k in range(3))}))
    rows.sort(key=lambda r: -r.games)
    return rows


# --------------------------------------------------------------- lane phase

@dataclass
class LaneSummary:
    role: str
    games: int
    diffs: dict[str, Mean]           # cs/gold/xp diff @10/@15
    early_death_rate: Rate           # games with ≥1 death before 14:00
    deaths_pre14: Mean
    kills_pre14: Mean
    solo_deaths: Mean
    fb_involved: Rate
    early_death_zones: list[tuple[str, int]]   # (zone label, count)
    early_death_minutes: list[float]


def lane_summary(games: list[Game], role: str) -> LaneSummary:
    diffs = {k: mean_se(col(games, k)) for k in
             ("cs_diff_10", "cs_diff_15", "gold_diff_10", "gold_diff_15",
              "xp_diff_10", "xp_diff_15")}
    ed = arr(col(games, "early_death"))
    zones, minutes = Counter(), []
    for _, p in games:
        for d in p.deaths:
            if d.t < 14:
                zones[RELATIVE_LABEL[relative_zone(d.zone, p.team)]] += 1
                minutes.append(d.t)
    fb = arr(col(games, "fb_involved"))
    return LaneSummary(
        role=role, games=len(games), diffs=diffs,
        early_death_rate=Rate(int(ed.sum()), len(ed)),
        deaths_pre14=mean_se(col(games, "deaths_pre14")),
        kills_pre14=mean_se(col(games, "kills_pre14")),
        solo_deaths=mean_se(col(games, "solo_deaths")),
        fb_involved=Rate(int(fb.sum()), len(fb)),
        early_death_zones=zones.most_common(),
        early_death_minutes=minutes)


@dataclass
class CurvePoint:
    minute: int
    mean: float
    lo: float
    hi: float
    n: int


def lane_curves(games: list[Game], max_minute: int = 20
                ) -> dict[str, dict[str, list[CurvePoint]]]:
    """{'gold'|'cs'|'xp': {'all'|'win'|'loss': [CurvePoint…]}}"""
    out = {}
    for metric in ("gold", "cs", "xp"):
        out[metric] = {}
        for split, sel in (("all", None), ("win", True), ("loss", False)):
            pts = []
            for minute in range(max_minute + 1):
                vals = [p.curves[metric][minute] for _, p in games
                        if (sel is None or p.win == sel)
                        and len(p.curves.get(metric, ())) > minute]
                m = mean_se(vals)
                if m.n:
                    pts.append(CurvePoint(minute, m.mean, m.lo, m.hi, m.n))
            out[metric][split] = pts
    return out


# ------------------------------------------------------------------ trends

TREND_KEYS = ("cs_per_min", "gold_diff_15", "cs_diff_10", "deaths_per_min",
              "deaths_pre14", "kp", "dmg_share", "vision_per_min", "kda")


@dataclass
class Trend:
    key: str
    label: str
    verdict: str            # improving | declining | stable | insufficient
    change: float           # fitted change over the window (last − first)
    slope: Slope
    rolling: list[tuple[int, float, float, int]]   # raw values, for charts
    first_third: Mean       # role-adjusted (you − role peer mean)
    last_third: Mean
    per_patch: list[tuple[str, Mean]]               # raw values
    # rolling average of the comparison group (role peer mean of the role
    # you played in each game) over the same windows as `rolling`: the line
    # your own line is judged against. None without a baseline.
    rolling_ref: list[tuple[int, float, float, int]] | None = None
    # no overall direction, but the newest third differs clearly from the
    # middle third (a U-shape a straight line can't see): "improving" /
    # "declining" / "rising" / "falling", else None
    recent: str | None = None
    middle_third: Mean | None = None


def trend(games: list[Game], key: str, baseline=None, window: int = 20,
          min_n: int = 30) -> Trend:
    """Verdict on ROLE-ADJUSTED values (your value − your role's peer mean
    in that game), so switching roles can't fake a trend: nine support
    games at the end would otherwise look like a CS/min collapse."""
    raw = col(games, key)
    y = raw
    ref_roll = None
    if baseline is not None:
        means = {}
        for _, p in games:
            if p.role and p.role not in means:
                v = baseline.values(p.role, key)
                means[p.role] = float(v.mean()) if len(v) else np.nan
        exp = np.array([means.get(p.role, np.nan) for _, p in games])
        y = raw - exp
        exp = np.where(np.isnan(raw), np.nan, exp)
        ref_roll = rolling_mean(exp, window)
    m = METRICS[key]
    s = ols_slope(y, conf=0.95)
    n = len(arr(y))
    third = max(len(y) // 3, 1)
    first, last = mean_se(y[:third]), mean_se(y[-third:])
    change = s.slope * (len(y) - 1) if not math.isnan(s.slope) else np.nan
    if n < min_n or math.isnan(s.slope):
        verdict = "insufficient"
    elif s.lo > 0 or s.hi < 0:
        up = s.lo > 0
        verdict = ("improving" if (up == (m.polarity > 0)) else "declining") \
            if m.polarity else ("rising" if up else "falling")
    else:
        verdict = "stable"
    patches: dict[str, list[float]] = defaultdict(list)
    for (mf, p), v in zip(games, raw):
        patches[mf.patch].append(v)
    middle = mean_se(y[third:len(y) - third]) if len(y) >= 3 * third         else None
    recent = None
    if verdict == "stable" and middle is not None and middle.n >= 10             and last.n >= 10 and not math.isnan(middle.se + last.se):
        d = last.mean - middle.mean
        if abs(d) > 1.96 * math.hypot(middle.se, last.se):
            up = d > 0
            recent = (("improving" if up == (m.polarity > 0) else
                       "declining") if m.polarity else
                      ("rising" if up else "falling"))
    per_patch = sorted(((pt, mean_se(v)) for pt, v in patches.items()),
                       key=lambda t: [int(x) for x in t[0].split(".")
                                      if x.isdigit()])
    return Trend(key, m.label, verdict, float(change), s,
                 rolling_mean(raw, window), first, last, per_patch,
                 ref_roll, recent, middle)


def score_series(scores: np.ndarray, window: int = 20):
    return rolling_mean(scores, min(window, max(len(scores), 1)))


# ----------------------------------------------------------- wins vs losses

DECIDE_KEYS = ("gold_diff_10", "gold_diff_15", "cs_diff_10", "cs_diff_15",
               "xp_diff_15", "deaths_pre14", "kills_pre14", "early_dmg",
               "turret_plates", "fb_involved", "solo_deaths", "cs_per_min",
               "vision_per_min", "control_wards", "kp", "dmg_share",
               "dmg_per_gold", "deaths_per_min", "deaths_ahead_rate",
               "deaths_before_enemy_obj", "epic_part", "first_legendary_min")


@dataclass
class Decider:
    key: str
    label: str
    early: bool
    effect: Effect


def wins_vs_losses(games: list[Game], keys=DECIDE_KEYS) -> list[Decider]:
    out = []
    for k in keys:
        w = [p.stats.get(k, np.nan) for _, p in games if p.win]
        l = [p.stats.get(k, np.nan) for _, p in games if not p.win]
        e = cohen_d(w, l)
        if not math.isnan(e.d):
            out.append(Decider(k, METRICS[k].label, METRICS[k].early, e))
    out.sort(key=lambda d: -abs(d.effect.d))
    return out


# --------------------------------------------------------- win-rate curves

@dataclass
class Bin:
    label: str
    rate: Rate


def wr_bins(games: list[Game], value, edges, labels) -> list[Bin]:
    buckets = [[0, 0] for _ in labels]
    for g in games:
        v = value(g)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        i = int(np.searchsorted(edges, v, side="right"))
        buckets[i][0] += g[1].win
        buckets[i][1] += 1
    return [Bin(lb, Rate(w, n)) for lb, (w, n) in zip(labels, buckets)]


def wr_curves(games: list[Game]) -> dict[str, list[Bin]]:
    return {
        "length": wr_bins(games, lambda g: g[0].duration_s / 60,
                          [20, 25, 30, 35, 40],
                          ["<20m", "20–25", "25–30", "30–35", "35–40", "40m+"]),
        "gd15": wr_bins(games, lambda g: g[1].stats.get("gold_diff_15"),
                        [-2000, -1000, 0, 1000, 2000],
                        ["≤−2k", "−2k…−1k", "−1k…0", "0…+1k", "+1k…+2k",
                         "≥+2k"]),
        "early_deaths": wr_bins(games,
                                lambda g: g[1].stats.get("deaths_pre14"),
                                [0.5, 1.5, 2.5], ["0", "1", "2", "3+"]),
        "cs10": wr_bins(games, lambda g: g[1].stats.get("cs_diff_10"),
                        [-5.5, 5.5], ["behind 6+", "within 5", "ahead 6+"]),
    }


# ---------------------------------------------------------------- matchups

@dataclass
class Matchup:
    opponent: str
    rate: Rate
    gd15: Mean
    cs10: Mean


def matchups(games: list[Game], min_games: int = 2) -> list[Matchup]:
    by: dict[str, list[Game]] = defaultdict(list)
    for g in games:
        if g[1].opponent_champion:
            by[g[1].opponent_champion].append(g)
    out = [Matchup(o, Rate(sum(p.win for _, p in gs), len(gs)),
                   mean_se(col(gs, "gold_diff_15")),
                   mean_se(col(gs, "cs_diff_10")))
           for o, gs in by.items() if len(gs) >= min_games]
    out.sort(key=lambda m: (-m.rate.n, m.opponent))
    return out


# ---------------------------------------------------------------- sessions

def session_info(all_games: list[Game], gap_min: float
                 ) -> dict[str, tuple[int, int, bool | None, int]]:
    """match_id -> (session #, game # within session, previous result in the
    session or None, consecutive losses right before this game)."""
    out = {}
    sess, num, prev_end, prev_win, streak = 0, 0, None, None, 0
    for m, p in all_games:
        if prev_end is None or (m.creation_ms - prev_end) / 60000 > gap_min:
            sess += 1
            num, prev_win, streak = 0, None, 0
        num += 1
        out[m.match_id] = (sess, num, prev_win, streak)
        streak = 0 if p.win else streak + 1
        prev_win, prev_end = p.win, m.end_ms
    return out


@dataclass
class SessionStats:
    by_game_number: list[Bin]
    after_losses: list[Bin]
    heatmap: list[list[Rate]]      # [weekday 0=Mon][hour 0..23]
    n_sessions: int


def sessions(games: list[Game], info) -> SessionStats:
    num_b = [[0, 0] for _ in range(5)]
    loss_b = [[0, 0] for _ in range(4)]
    heat = [[[0, 0] for _ in range(24)] for _ in range(7)]
    sess_ids = set()
    for m, p in games:
        s, num, _, streak = info[m.match_id]
        sess_ids.add(s)
        i = min(num, 5) - 1
        num_b[i][0] += p.win
        num_b[i][1] += 1
        j = min(streak, 3)
        loss_b[j][0] += p.win
        loss_b[j][1] += 1
        t = datetime.fromtimestamp(m.creation_ms / 1000)
        heat[t.weekday()][t.hour][0] += p.win
        heat[t.weekday()][t.hour][1] += 1
    return SessionStats(
        [Bin(lb, Rate(*v)) for lb, v in zip(
            ["1st", "2nd", "3rd", "4th", "5th+"], num_b)],
        [Bin(lb, Rate(*v)) for lb, v in zip(
            ["after a win / first", "after 1 loss", "after 2 losses",
             "after 3+ losses"], loss_b)],
        [[Rate(*c) for c in row] for row in heat], len(sess_ids))


# ------------------------------------------------------------------ deaths

PHASES = (("0–14", 0, 14), ("14–25", 14, 25), ("25+", 25, 999))


@dataclass
class DeathStats:
    points: list[tuple[float, int, int, str, bool]]  # t, x, y, phase, own team blue?
    by_minute: list[int]
    per_game: Mean
    followed_by_objective: Rate
    while_ahead: Rate
    solo: Rate
    zones_by_phase: dict[str, list[tuple[str, int]]]


def deaths(games: list[Game]) -> DeathStats:
    pts, by_min = [], [0] * 61
    zones = {ph: Counter() for ph, _, _ in PHASES}
    total = obj = ahead = solo = 0
    for _, p in games:
        for d in p.deaths:
            ph = next(name for name, a, b in PHASES if a <= d.t < b)
            pts.append((d.t, d.x, d.y, ph, p.team == 100))
            by_min[min(int(d.t), 60)] += 1
            zones[ph][RELATIVE_LABEL[relative_zone(d.zone, p.team)]] += 1
            total += 1
            obj += d.before_enemy_objective
            ahead += d.while_ahead
            solo += d.solo
    with_tl = [p for m, p in games if m.has_timeline]
    return DeathStats(
        pts, by_min, mean_se([p.stats["deaths"] for _, p in games]),
        Rate(obj, total), Rate(ahead, total), Rate(solo, total),
        {ph: z.most_common() for ph, z in zones.items()})


# -------------------------------------------------------------------- items

@dataclass
class ItemTiming:
    points: list[tuple[float, bool]]
    bins: list[Bin]
    mean: Mean


def item_timing(games: list[Game]) -> ItemTiming:
    pts = [(p.stats["first_legendary_min"], p.win) for _, p in games
           if not math.isnan(p.stats.get("first_legendary_min", np.nan))]
    bins = wr_bins(games, lambda g: g[1].stats.get("first_legendary_min"),
                   [12, 14, 16, 18], ["<12m", "12–14", "14–16", "16–18",
                                      "18m+"])
    return ItemTiming(pts, bins, mean_se([t for t, _ in pts]))


# --------------------------------------------------------------------- duos

@dataclass
class Duo:
    riot_id: str
    puuid: str
    rate: Rate
    score: Mean


def duos(games: list[Game], scores: np.ndarray, min_games: int = 3
         ) -> list[Duo]:
    by: dict[str, list[int]] = defaultdict(list)
    names = {}
    for i, (m, p) in enumerate(games):
        for q in m.players:
            if q.team == p.team and q.puuid != p.puuid and q.puuid:
                by[q.puuid].append(i)
                names[q.puuid] = q.riot_id
    out = [Duo(names[pu], pu, Rate(sum(games[i][1].win for i in ix), len(ix)),
               mean_se(scores[ix]))
           for pu, ix in by.items() if len(ix) >= min_games]
    out.sort(key=lambda d: -d.rate.n)
    return out
