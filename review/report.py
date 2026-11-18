"""Terminal rendering of an AnalysisResult (milestone-2 check-in view)."""
from __future__ import annotations

import math
from datetime import datetime

from .analysis import ROLE_LABEL, AnalysisResult, fmt_value
from .analysis.radar import fmt_score
from .analysis.stats import Rate

W = 78


def _h(title: str) -> str:
    return "\n◆ %s %s" % (title, "─" * (W - len(title) - 3))


def _wr(r: Rate) -> str:
    if not r.n:
        return "—"
    lo, hi = r.ci
    return "%3.0f%% [%2.0f–%2.0f] n=%d" % (100 * r.p, 100 * lo, 100 * hi, r.n)


def _m(mean, fmt="%+.0f") -> str:
    if mean.n == 0 or math.isnan(mean.mean):
        return "—"
    s = fmt % mean.mean
    if math.isnan(mean.se):
        return s
    return "%s ±%s" % (s, fmt.replace("+", "") % mean.half)


def _signed(key: str, v: float) -> str:
    s = fmt_value(key, v)
    return s if s.startswith(("+", "-", "—")) else "+" + s


def _bar(score: float, width: int = 20) -> str:
    if math.isnan(score):
        return " " * width
    n = int(round(score / 100 * width))
    return "█" * n + "·" * (width - n)


def render(r: AnalysisResult, riot_id: str, region: str,
           rank_line: str = "") -> str:
    out = []
    f = r.filter
    scope = " · ".join(x for x in (
        "Solo/Duo" if f.queues == (420,) else "Flex" if f.queues == (440,)
        else "Ranked", ROLE_LABEL.get(f.role, "") if f.role else "",
        f.champion or "") if x)
    out.append("REVIEW · %s · %s%s" % (riot_id, region,
                                         " · " + rank_line if rank_line else ""))
    out.append("%s · %d games · record %dW %dL · WR %s" % (
        scope, r.n_games, r.record.wins, r.record.n - r.record.wins,
        _wr(r.record)))
    ex = r.excluded
    out.append("excluded: %d remakes, %d other queues · %d games without "
               "timeline · peer pool: %s" % (
                   ex.remakes, ex.other_queues, ex.no_timeline,
                   ", ".join("%s %d" % (ROLE_LABEL[k], v)
                             for k, v in r.peer_counts.items())))
    out.append("Intervals are 90 %. Scores: 50 = a typical same-role player "
               "in your games.")

    # radar
    R = r.radar
    if R:
        out.append(_h("PERFORMANCE RADAR"))
        out.append("Overall  %.0f [%.0f–%.0f]%s" % (
            R.overall, R.overall_lo, R.overall_hi,
            "   (INSUFFICIENT DATA)" if R.insufficient else ""))
        prev = {a.axis: a.score for a in r.previous_radar.axes} \
            if r.previous_radar else {}
        for a in R.axes:
            delta = ""
            if a.axis in prev and not math.isnan(prev[a.axis]):
                delta = " (1st half %.0f)" % prev[a.axis]
            out.append("  %-12s %s %-12s n=%-3d%s" % (
                a.label, _bar(a.score), fmt_score(a), a.n, delta))
        out.append("\n  Sub-metric percentiles (you vs role-matched peer "
                   "median):")
        for a in R.axes:
            out.append("  %s" % a.label.upper())
            for s in a.subs:
                out.append("    %-40s %5.1f  you %8s  peers %8s  n=%d" % (
                    s.label, s.score, s.value_text, s.peer_text, s.n))
        if r.score_series:
            pts = [m for _, m, _, _ in r.score_series[::max(
                len(r.score_series) // 12, 1)]]
            out.append("  Game score, rolling 20: " + " → ".join(
                "%.0f" % p for p in pts))

    def findings(title, fs):
        out.append(_h(title))
        if not fs:
            out.append("  (nothing passes the confidence bar)")
        for i, x in enumerate(fs, 1):
            out.append("  %d. [%s] %s" % (i, x.confidence, x.text))
            if x.detail:
                out.append("     %s" % x.detail)
            if x.tip:
                out.append("     → %s" % x.tip)
    findings("STRENGTHS", r.strengths)
    findings("WEAKNESSES", r.weaknesses)

    out.append(_h("WHAT DECIDES YOUR GAMES  (Cohen's d, wins − losses)"))
    out.append("  'early' stats are measured before the game is decided, so "
               "they're better\n  evidence of cause than whole-game stats.")
    for dd in r.deciders[:10]:
        e = dd.effect
        out.append("  %-42s d=%+.2f [%+.2f, %+.2f]  W %s / L %s%s" % (
            dd.label, e.d, e.lo, e.hi, fmt_value(dd.key, e.mean_a),
            fmt_value(dd.key, e.mean_b), "  early" if dd.early else ""))

    out.append(_h("TRENDS  (role-adjusted; OLS slope 95% CI must exclude 0)"))
    out.append("  values are you minus your role's peer average in each game")
    for t in r.trends:
        if t.verdict == "insufficient":
            continue
        out.append("  %-30s %-10s %s over %d games  (first third %s → last "
                   "third %s)" % (
                       t.label, t.verdict.upper(), _signed(t.key, t.change),
                       t.slope.n, _signed(t.key, t.first_third.mean),
                       _signed(t.key, t.last_third.mean)))

    out.append(_h("LANE PHASE"))
    for role, L in sorted(r.lane.items(), key=lambda kv: -kv[1].games):
        if L.games < 5:
            continue
        d = L.diffs
        out.append("  %s (%d games)  CS@10 %s  CS@15 %s  GD@10 %s  GD@15 %s  "
                   "XP@15 %s" % (
                       ROLE_LABEL[role], L.games, _m(d["cs_diff_10"], "%+.1f"),
                       _m(d["cs_diff_15"], "%+.1f"), _m(d["gold_diff_10"]),
                       _m(d["gold_diff_15"]), _m(d["xp_diff_15"])))
        out.append("    early death in %s · deaths<14 %s · solo deaths %s · "
                   "FB involvement %s" % (
                       _wr(L.early_death_rate),
                       _m(L.deaths_pre14, "%.2f"), _m(L.solo_deaths, "%.2f"),
                       _wr(L.fb_involved)))
        if L.early_death_zones:
            out.append("    early deaths by zone: " + ", ".join(
                "%s %d" % z for z in L.early_death_zones[:5]))

    out.append(_h("CHAMPIONS"))
    out.append("  %-12s %5s  %-20s %5s %6s %10s %8s %10s" % (
        "Champion", "Games", "WR [90% CI]", "KDA", "CS/m", "GD@15", "DMG%",
        "Score"))
    for c in r.champions[:12]:
        out.append("  %-12s %5d  %-20s %5.2f %6.2f %10s %7.0f%% %10s" % (
            c.name, c.games, _wr(c.rate).rsplit(" n=", 1)[0], c.kda,
            c.cs_per_min.mean, _m(c.gd15), 100 * c.dmg_share.mean,
            _m(c.score, "%.0f")))

    out.append(_h("WIN RATE CURVES"))
    for name, title in (("gd15", "by gold diff @15"),
                        ("early_deaths", "by deaths before 14:00"),
                        ("length", "by game length"),
                        ("cs10", "by CS diff @10")):
        out.append("  %s: " % title + " · ".join(
            "%s %s" % (b.label, _wr(b.rate)) for b in r.wr_curves[name]
            if b.rate.n))

    s = r.sessions
    out.append(_h("SESSIONS & TILT  (%d sessions, >60 min gap = new "
                  "session)" % s.n_sessions))
    out.append("  game # in session: " + " · ".join(
        "%s %s" % (b.label, _wr(b.rate)) for b in s.by_game_number if b.rate.n))
    out.append("  streaks: " + " · ".join(
        "%s %s" % (b.label, _wr(b.rate)) for b in s.after_losses if b.rate.n))

    dth = r.deaths
    out.append(_h("DEATHS"))
    out.append("  %s deaths/game · %.0f%% followed by an enemy objective "
               "≤60 s · %.0f%% solo · %.0f%% while ahead" % (
                   _m(dth.per_game, "%.1f"),
                   100 * dth.followed_by_objective.p, 100 * dth.solo.p,
                   100 * dth.while_ahead.p))
    for ph, zones in dth.zones_by_phase.items():
        if zones:
            out.append("  %-6s %s" % (ph, ", ".join("%s %d" % z
                                                    for z in zones[:4])))

    it = r.items
    out.append(_h("FIRST LEGENDARY ITEM"))
    if it.mean.n:
        out.append("  average %.1f min (n=%d) · " % (it.mean.mean, it.mean.n)
                   + " · ".join("%s %s" % (b.label, _wr(b.rate))
                                for b in it.bins if b.rate.n))
    else:
        out.append("  (item data unavailable)")

    out.append(_h("MATCHUPS  (≥3 games vs that lane opponent)"))
    for mu in [m for m in r.matchups if m.rate.n >= 3][:10]:
        out.append("  vs %-12s %s  GD@15 %s" % (mu.opponent, _wr(mu.rate),
                                               _m(mu.gd15)))

    out.append(_h("DUO PARTNERS  (≥3 games together)"))
    if not r.duos:
        out.append("  none")
    for du in r.duos[:6]:
        out.append("  %-24s %s  avg score %s" % (du.riot_id, _wr(du.rate),
                                                 _m(du.score, "%.0f")))

    out.append(_h("LATEST GAMES"))
    for g in r.games[:8]:
        out.append("  %s %-10s %-7s %s %2d/%2d/%2d  %5.1f cs/m  GD@15 %6s  "
                   "score %3.0f" % (
                       datetime.fromtimestamp(g.creation_ms / 1000).strftime(
                           "%m-%d %H:%M"), g.champion,
                       ROLE_LABEL.get(g.role, "?"), "W" if g.win else "L",
                       g.k, g.d, g.a, g.cs_per_min,
                       "—" if math.isnan(g.gd15) else "%+.0f" % g.gd15,
                       g.score))
    out.append("\nDone · %d games · %s · %s" % (r.n_games, scope, region))
    return "\n".join(out)
