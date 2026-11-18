"""The analysis engine: one pure function from cached games to a result.

    analyze(matches, puuid, Filter(role="BOTTOM")) -> AnalysisResult

Input: extracted MatchFeatures for the player's cached games (any order, any
queue — filtering happens here). Output: a structured result the CLI and UI
render. No I/O, no globals; deterministic (seeded bootstrap).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from . import aggregates as agg
from .findings import (Finding, champion_findings, rank_findings,
                       stat_findings)
from .model import ROLE_LABEL, ROLES, MatchFeatures
from .peers import PeerBaseline
from .radar import Radar, RadarConfig, RadarModel, load_config
from .stats import Rate, mean_se

SR_MAP = 11


@dataclass(frozen=True)
class Filter:
    queues: tuple[int, ...] = (420,)
    role: str | None = None          # one of ROLES
    champion: str | None = None
    last_n: int | None = None
    since_ms: int | None = None

    def key(self) -> str:
        return repr(self)


@dataclass
class Exclusions:
    remakes: int = 0
    other_queues: int = 0
    not_in_game: int = 0
    no_timeline: int = 0
    unknown_role: int = 0


@dataclass
class GameRow:
    match_id: str
    creation_ms: int
    duration_s: int
    patch: str
    champion: str
    role: str | None
    opponent: str | None
    win: bool
    k: int
    d: int
    a: int
    cs: int
    cs_per_min: float
    gd15: float
    score: float


@dataclass
class AnalysisResult:
    puuid: str
    filter: Filter
    n_games: int
    excluded: Exclusions
    record: Rate
    radar: Radar | None
    previous_radar: Radar | None            # first half of the sample
    score_series: list
    roles: list
    champions: list
    lane: dict
    lane_curves: dict
    trends: list
    deciders: list
    strengths: list[Finding]
    weaknesses: list[Finding]
    wr_curves: dict
    matchups: list
    sessions: object
    deaths: object
    items: object
    duos: list
    games: list[GameRow] = field(default_factory=list)
    peer_counts: dict = field(default_factory=dict)
    # per-game detail (chronological) for drill-downs and the game page
    chron_ids: list = field(default_factory=list)
    game_axes: dict = field(default_factory=dict)     # mid -> [7 axes, score]
    axis_series: dict = field(default_factory=dict)   # axis -> [per game]
    sub_series: dict = field(default_factory=dict)    # metric -> [pct/game]
    champion_radars: dict = field(default_factory=dict)  # champ -> (n, [8])
    role_radars: dict = field(default_factory=dict)      # role -> (n, [8])
    distributions: dict = field(default_factory=dict)    # key -> (me, peers)
    dist_role: str | None = None
    key_stats: list = field(default_factory=list)
    lane_by_champion: dict = field(default_factory=dict)  # champ -> summary
    # None = the default baseline (same-role players in YOUR games);
    # otherwise the benchmark's label, e.g. "Master+"
    reference_label: str | None = None
    reference_games: int = 0
    # "where you should be": the comparison group's role-matched mean for
    # every metric (each of your games contributes its own role's mean)
    peer_means: dict = field(default_factory=dict)


def _eligible(matches, puuid, queues, ex: Exclusions):
    out = []
    for m in matches:
        if m.map_id != SR_MAP or m.queue_id not in queues:
            ex.other_queues += 1
            continue
        if m.remake:
            ex.remakes += 1
            continue
        p = m.player(puuid)
        if p is None:
            ex.not_in_game += 1
            continue
        out.append((m, p))
    out.sort(key=lambda g: g[0].creation_ms)
    return out


def analyze(matches: list[MatchFeatures], puuid: str,
            flt: Filter = Filter(), cfg: RadarConfig | None = None,
            compute_ci: bool = True,
            reference: list[MatchFeatures] | None = None,
            reference_label: str | None = None) -> AnalysisResult:
    """``reference``: score against these games' players (a benchmark such
    as Master+) instead of the same-role players in your own games. That
    puts every account on one shared scale."""
    cfg = cfg or load_config()
    ex = Exclusions()
    every = _eligible(matches, puuid, flt.queues, ex)
    # the peer pool always uses ALL your eligible games (bigger, fairer)
    if reference is not None:
        ref = [m for m in reference if not m.remake
               and m.queue_id in (420, 440)]
        baseline = PeerBaseline(ref, puuid)
    else:
        ref = None
        baseline = PeerBaseline([m for m, _ in every], puuid)
    model = RadarModel(baseline, cfg)
    sess_info = agg.session_info(every, cfg.session_gap_min)

    games = every
    if flt.role:
        games = [g for g in games if g[1].role == flt.role]
    if flt.champion:
        games = [g for g in games if g[1].champion == flt.champion]
    if flt.since_ms:
        games = [g for g in games if g[0].creation_ms >= flt.since_ms]
    if flt.last_n:
        games = games[-flt.last_n:]
    ex.no_timeline = sum(not m.has_timeline for m, _ in games)
    ex.unknown_role = sum(p.role is None for _, p in games)

    pgs = [p for _, p in games]
    prev = [sess_info[m.match_id][2] for m, _ in games]
    scored = model.score_games(pgs) if pgs else None
    scores = scored[2] if scored else np.array([])

    radar = prev_radar = None
    if pgs:
        radar = model.radar(pgs, prev, compute_ci, scored=scored)
        half = len(pgs) // 2
        if half >= cfg.min_games:
            prev_radar = model.radar(pgs[:half], prev[:half], compute_ci=False)

    extra = _detail(model, baseline, games, pgs, prev, scored, cfg)

    found = stat_findings(games, baseline, peer_word=(
        "%s players" % reference_label) if ref is not None else None) +         champion_findings(games)
    strengths, weaknesses = rank_findings(found)

    role_rows = agg.group_rows(games, scores,
                               lambda m, p: ROLE_LABEL.get(p.role, "?"))
    champ_rows = agg.group_rows(games, scores, lambda m, p: p.champion)
    lane = {}
    for role in ROLES:
        rg = [g for g in games if g[1].role == role]
        if rg:
            lane[role] = agg.lane_summary(rg, role)
    lane_champ = {}
    for c in Counter(p.champion for _, p in games):
        cg = [g for g in games if g[1].champion == c]
        if len(cg) >= 3:
            roles = Counter(p.role for _, p in cg).most_common(1)[0][0]
            lane_champ[c] = agg.lane_summary(cg, roles or "")

    rows = [GameRow(m.match_id, m.creation_ms, m.duration_s, m.patch,
                    p.champion, p.role, p.opponent_champion, p.win,
                    int(p.stats["kills"]), int(p.stats["deaths"]),
                    int(p.stats["assists"]), int(p.stats["cs"]),
                    p.stats["cs_per_min"], p.stats["gold_diff_15"],
                    float(s))
            for (m, p), s in zip(games, scores)]

    return AnalysisResult(
        puuid=puuid, filter=flt, n_games=len(games), excluded=ex,
        record=Rate(sum(p.win for p in pgs), len(pgs)),
        radar=radar, previous_radar=prev_radar,
        score_series=agg.score_series(scores) if len(scores) else [],
        roles=role_rows, champions=champ_rows, lane=lane,
        lane_curves=agg.lane_curves(games),
        trends=[agg.trend(games, k, baseline) for k in agg.TREND_KEYS],
        deciders=agg.wins_vs_losses(games),
        strengths=strengths, weaknesses=weaknesses,
        wr_curves=agg.wr_curves(games), matchups=agg.matchups(games),
        sessions=agg.sessions(games, sess_info), deaths=agg.deaths(games),
        items=agg.item_timing(games), duos=agg.duos(games, scores),
        games=rows[::-1],
        peer_counts={r: baseline.n(r) for r in ROLES},
        lane_by_champion=lane_champ,
        peer_means=_peer_means(baseline, games),
        reference_label=reference_label if ref is not None else None,
        reference_games=len(ref) if ref is not None else 0, **extra)


def _peer_means(baseline, games) -> dict:
    from .metrics import METRICS
    rs = [p.role for _, p in games]
    out = {}
    for k in METRICS:
        try:
            m = baseline.role_matched(k, rs)[0]
        except Exception:
            continue
        if m == m:                      # not NaN
            out[k] = float(m)
    return out


KEY_STATS = ("kda", "cs_per_min", "gold_diff_15", "kp", "dmg_share",
             "vision_per_min", "deaths_pre14", "first_legendary_min")
DIST_KEYS = ("cs_per_min", "kp", "dmg_share", "deaths", "vision_per_min",
             "gold_per_min")


def _detail(model, baseline, games, pgs, prev, scored, cfg) -> dict:
    from .metrics import METRICS
    from .radar import SCORED
    out: dict = {"chron_ids": [m.match_id for m, _ in games]}
    if not pgs:
        return out
    P, A, g = scored
    out["game_axes"] = {m.match_id: [float(v) for v in A[i]] + [float(g[i])]
                        for i, (m, _) in enumerate(games)}
    out["axis_series"] = {ax: [float(v) for v in A[:, j]]
                          for j, ax in enumerate(SCORED)}
    out["sub_series"] = {k: [float(v) for v in P[:, j]]
                         for k, j in model.kidx.items()}

    def radar_of(sel):
        ix = [i for i, (_, p) in enumerate(games) if sel(p)]
        if len(ix) < 3:
            return None
        r = model.radar([pgs[i] for i in ix], [prev[i] for i in ix],
                        compute_ci=False)
        return len(ix), [a.score for a in r.axes]
    champs = Counter(p.champion for p in pgs)
    out["champion_radars"] = {c: rr for c in champs
                              if (rr := radar_of(lambda p, c=c:
                                                 p.champion == c))}
    out["role_radars"] = {r: rr for r in ROLES
                          if (rr := radar_of(lambda p, r=r: p.role == r))}
    roles = Counter(p.role for p in pgs if p.role)
    if roles:
        main = roles.most_common(1)[0][0]
        out["dist_role"] = main
        out["distributions"] = {
            k: ([p.stats.get(k, np.nan) for p in pgs if p.role == main],
                [q.stats.get(k, np.nan) for q in baseline.games[main]])
            for k in DIST_KEYS}
    ks = []
    rs = [p.role for p in pgs]
    for k in KEY_STATS:
        vals = [p.stats.get(k, np.nan) for p in pgs]
        m = mean_se(vals)
        peer = baseline.role_matched(k, rs)[0]
        if k == "kda":   # pooled, as on the scoreboard
            ka = sum(p.stats["kills"] + p.stats["assists"] for p in pgs)
            m.mean = ka / max(sum(p.stats["deaths"] for p in pgs), 1)
            pp = [q for r in set(r for r in rs if r)
                  for q in baseline.games[r]]
            peer = sum(q.stats["kills"] + q.stats["assists"] for q in pp) /                 max(sum(q.stats["deaths"] for q in pp), 1)
        ks.append((k, METRICS[k].label, m, peer, METRICS[k].polarity))
    out["key_stats"] = ks
    return out
