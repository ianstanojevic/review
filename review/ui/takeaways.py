"""One-line plain-English takeaways computed from the data above each chart.
Every sentence carries its n; nothing is said about bins with < 5 games."""
from __future__ import annotations

import math

from ..analysis import fmt_value
from ..analysis.stats import Rate

MIN_N = 5


def _pool(bins, labels) -> Rate:
    w = sum(b.rate.wins for b in bins if b.label in labels)
    n = sum(b.rate.n for b in bins if b.label in labels)
    return Rate(w, n)


def _pct(r: Rate) -> str:
    return "%.0f%%" % (100 * r.p)


def wr_gd15(res) -> str:
    b = res.wr_curves["gd15"]
    ahead = _pool(b, {"0…+1k", "+1k…+2k", "≥+2k"})
    behind = _pool(b, {"≤−2k", "−2k…−1k", "−1k…0"})
    if ahead.n < MIN_N or behind.n < MIN_N:
        return "Not enough games with a lane opponent yet."
    return ("You win %s when ahead in gold at 15:00 (n=%d), %s when behind "
            "(n=%d)." % (_pct(ahead), ahead.n, _pct(behind), behind.n))


def wr_cs10(res) -> str:
    b = {x.label: x.rate for x in res.wr_curves["cs10"]}
    a, z = b.get("ahead 6+"), b.get("behind 6+")
    if not a or not z or a.n < MIN_N or z.n < MIN_N:
        return "Not enough games yet."
    return ("You win %s when 6+ CS ahead at 10:00 (n=%d), %s when 6+ behind "
            "(n=%d)." % (_pct(a), a.n, _pct(z), z.n))


def wr_early_deaths(res) -> str:
    b = {x.label: x.rate for x in res.wr_curves["early_deaths"]}
    z, t = b.get("0"), b.get("3+")
    if not z or not t or z.n < MIN_N or t.n < MIN_N:
        return "Not enough games yet."
    return ("With no deaths before 14:00 you win %s (n=%d); with 3+ early "
            "deaths, %s (n=%d)." % (_pct(z), z.n, _pct(t), t.n))


def wr_length(res) -> str:
    bins = [b for b in res.wr_curves["length"] if b.rate.n >= 8]
    if len(bins) < 2:
        return "Not enough games yet."
    best = max(bins, key=lambda b: b.rate.p)
    worst = min(bins, key=lambda b: b.rate.p)
    return ("Best game length: %s (%s, n=%d); worst: %s (%s, n=%d)."
            % (best.label, _pct(best.rate), best.rate.n, worst.label,
               _pct(worst.rate), worst.rate.n))


def lane_curve(res, metric: str) -> str:
    c = res.lane_curves[metric]
    unit = {"gold": "gold", "cs": "CS", "xp": "XP"}[metric]
    w = next((p for p in c["win"] if p.minute == 15), None)
    l = next((p for p in c["loss"] if p.minute == 15), None)
    if not w or not l:
        return "Not enough games with a lane opponent yet."
    fmt = "%+.0f" if metric != "cs" else "%+.1f"
    return ("At 15:00 you're %s %s vs your lane opponent in wins (n=%d) and "
            "%s in losses (n=%d)." % (fmt % w.mean, unit, w.n, fmt % l.mean,
                                      l.n))


def trend(t) -> str:
    if t.verdict == "insufficient":
        return "Not enough games for a trend verdict (need 30)."
    word = {"improving": "Improving", "declining": "Declining",
            "stable": "Stable", "rising": "Rising", "falling": "Falling"}[
        t.verdict]
    ch = fmt_value(t.key, t.change)
    if not ch.startswith(("+", "-", "—")):
        ch = "+" + ch
    tail = ("95% CI of the slope excludes zero" if t.verdict != "stable"
            else "change within the noise")
    return "%s: %s %s over %d games vs role peers (%s)." % (
        word, t.label, ch, t.slope.n, tail)


def deciders(res) -> str:
    early = [d for d in res.deciders if d.early]
    if not res.deciders:
        return "Not enough wins and losses yet."
    top = res.deciders[0]
    s = "%s separates your wins from losses most (d = %+.2f)." % (
        top.label, top.effect.d)
    if early and early[0] is not top:
        s += " Strongest early signal: %s (d = %+.2f)." % (
            early[0].label, early[0].effect.d)
    return s


def champions(res) -> str:
    rows = [c for c in res.champions if c.games >= 5]
    if not rows:
        return "Play at least 5 games on a champion to rate it."
    best = max(rows, key=lambda c: c.rate.ci[0])
    worst = min(rows, key=lambda c: c.rate.ci[1])
    from .screens.base import cname
    return ("Most reliable: %s (%s, n=%d). Most worrying: %s (%s, n=%d)."
            % (cname(best.name), _pct(best.rate), best.games,
               cname(worst.name), _pct(worst.rate), worst.games))


def session_number(res) -> str:
    b = [x for x in res.sessions.by_game_number if x.rate.n >= MIN_N]
    if len(b) < 2:
        return "Not enough sessions yet."
    first = b[0]
    worst = min(b[1:], key=lambda x: x.rate.p)
    return ("Your %s game of a session wins %s (n=%d) vs %s for the 1st "
            "(n=%d)." % (worst.label, _pct(worst.rate), worst.rate.n,
                         _pct(first.rate), first.rate.n))


def streaks(res) -> str:
    b = [x for x in res.sessions.after_losses if x.rate.n >= MIN_N]
    if len(b) < 2:
        return "Not enough loss streaks yet."
    worst = min(b, key=lambda x: x.rate.p)
    return "%s you win %s (n=%d)." % (worst.label.capitalize(),
                                       _pct(worst.rate), worst.rate.n)


def heatmap(res) -> str:
    cells = []
    for d, rowv in enumerate(res.sessions.heatmap):
        for h, r in enumerate(rowv):
            if r.n >= MIN_N:
                cells.append((r.p, d, h, r))
    if len(cells) < 2:
        return "Not enough games per time slot yet (need 5 in a slot)."
    days = "Mon Tue Wed Thu Fri Sat Sun".split()
    best, worst = max(cells), min(cells)
    return ("Best slot: %s %02d:00 (%s, n=%d). Worst: %s %02d:00 (%s, n=%d)."
            % (days[best[1]], best[2], _pct(best[3]), best[3].n,
               days[worst[1]], worst[2], _pct(worst[3]), worst[3].n))


def deaths(res) -> str:
    d = res.deaths
    if not d.followed_by_objective.n:
        return "No deaths recorded."
    return ("%.0f%% of your deaths are followed by an enemy objective within "
            "60 s (n=%d deaths)." % (100 * d.followed_by_objective.p,
                                     d.followed_by_objective.n))


def death_minutes(res) -> str:
    bm = res.deaths.by_minute
    if not sum(bm):
        return "No deaths recorded."
    peak = max(range(len(bm)), key=lambda i: bm[i])
    early = sum(bm[:14]) / sum(bm)
    return ("You die most around minute %d; %.0f%% of deaths happen before "
            "14:00." % (peak, 100 * early))


def items(res) -> str:
    b = [x for x in res.items.bins if x.rate.n >= MIN_N]
    if len(b) < 2:
        return "Not enough item data yet."
    fast, slow = b[0], b[-1]
    return ("First legendary %s: %s WR (n=%d); %s: %s (n=%d)." % (
        fast.label, _pct(fast.rate), fast.rate.n, slow.label,
        _pct(slow.rate), slow.rate.n))


def duos(res) -> str:
    if not res.duos:
        return "No teammate appears in 3+ of these games."
    d = res.duos[0]
    return "Most frequent duo: %s — %s WR together (n=%d) vs %s overall." % (
        d.riot_id.split("#")[0], _pct(d.rate), d.rate.n, _pct(res.record))


def distribution(res, key: str, label: str) -> str:
    me, peers = res.distributions.get(key, ([], []))
    me = sorted(v for v in me if v == v)
    peers = sorted(v for v in peers if v == v)
    if len(me) < MIN_N or len(peers) < MIN_N:
        return "Not enough games yet."
    m, p = me[len(me) // 2], peers[len(peers) // 2]
    from ..analysis import ROLE_LABEL
    grp = ("%s %s players" % (res.reference_label,
                              ROLE_LABEL.get(res.dist_role, ""))
           if getattr(res, "reference_label", None)
           else "%s peers" % ROLE_LABEL.get(res.dist_role, ""))
    return "Your median %s is %s vs %s for %s (n=%d / %d)." % (
        label, fmt_value(key, m), fmt_value(key, p), grp, len(me),
        len(peers))


def matchups(res) -> str:
    m = [x for x in res.matchups if x.rate.n >= 4]
    if len(m) < 2:
        return "Not enough repeated lane opponents yet."
    from .screens.base import cname
    best = max(m, key=lambda x: x.rate.p)
    worst = min(m, key=lambda x: x.rate.p)
    return "Best matchup: vs %s (%s, n=%d). Worst: vs %s (%s, n=%d)." % (
        cname(best.opponent), _pct(best.rate), best.rate.n,
        cname(worst.opponent), _pct(worst.rate), worst.rate.n)
