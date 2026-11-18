"""Command line: fetch a player's ranked games and print raw stats.

    python -m review.cli fetch "flintgnuggaren#ian" --region EUW --count 150
    python -m review.cli fetch "flintgnuggaren#ian" --offline   # cache only
    python -m review.cli cache

The stats printed here are deliberately raw (milestone 1). The real analysis
— peer baselines, radar, findings — is the analysis module (milestone 2).
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

from . import config
from .cache import Cache
from .riot_client import (PRIMARY_REGIONS, REGIONS, ReviewError,
                          default_client, get_region, parse_riot_id)
from .sync import DEFAULT_GAMES, QUEUE_LABEL, QUEUES, Progress, SyncJob

ROLE_LABEL = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid",
              "BOTTOM": "Bot", "UTILITY": "Support", "": "?"}
TIER_LABEL = {"IRON": "Iron", "BRONZE": "Bronze", "SILVER": "Silver",
              "GOLD": "Gold", "PLATINUM": "Platinum", "EMERALD": "Emerald",
              "DIAMOND": "Diamond", "MASTER": "Master",
              "GRANDMASTER": "Grandmaster", "CHALLENGER": "Challenger"}
REMAKE_SECONDS = 300


def wilson(wins: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """90 % Wilson score interval for a win rate."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def fmt_wr(wins: int, n: int) -> str:
    lo, hi = wilson(wins, n)
    return "%5.1f%% ±%4.1f" % (100 * wins / n, 100 * (hi - lo) / 2) if n else "—"


def fmt_dur(s: float) -> str:
    return "%d:%02d" % (s // 60, s % 60)


def fmt_rank(e: dict) -> str:
    tier = TIER_LABEL.get(e.get("tier", ""), e.get("tier", "?"))
    div = "" if e.get("tier") in ("MASTER", "GRANDMASTER", "CHALLENGER") \
        else " " + e.get("rank", "")
    w, l = e.get("wins", 0), e.get("losses", 0)
    return "%s%s %d LP  (%dW %dL, %.1f%%)" % (
        tier, div, e.get("leaguePoints", 0), w, l,
        100 * w / max(w + l, 1))


def is_remake(info: dict) -> bool:
    return (info.get("gameDuration", 0) < REMAKE_SECONDS
            or any(p.get("gameEndedInEarlySurrender")
                   for p in info.get("participants", [])))


# ---------------------------------------------------------------- printing

class ProgressPrinter:
    def __init__(self):
        self.tty = sys.stdout.isatty()
        self._last_line = 0

    def __call__(self, p: Progress) -> None:
        eta = (" · ~%s left" % fmt_dur(p.eta_s)) if p.eta_s else ""
        line = p.message + eta
        if p.phase == "games" and p.total:
            filled = int(24 * p.done / p.total)
            line = "[%s%s] %s" % ("█" * filled, "·" * (24 - filled), line)
            if self.tty:
                sys.stdout.write("\r" + line.ljust(self._last_line))
                sys.stdout.flush()
                self._last_line = len(line)
                if p.done == p.total:
                    sys.stdout.write("\n")
                return
            if p.done % 10 and p.done != p.total:
                return       # non-interactive: one line every 10 games
        elif self._last_line and self.tty:
            sys.stdout.write("\n")
            self._last_line = 0
        print(line, flush=True)


def summarize(cache: Cache, puuid: str, match_ids: list[str], label: str,
              region: str) -> None:
    games, remakes, no_tl = [], 0, 0
    without_tl = cache.timelines_unavailable(match_ids)
    for mid in match_ids:
        m = cache.get_match(mid)
        if m is None:
            continue
        info = m["info"]
        if is_remake(info):
            remakes += 1
            continue
        me = next((p for p in info["participants"] if p["puuid"] == puuid),
                  None)
        if me is None:
            continue
        if mid in without_tl:
            no_tl += 1
        games.append((info, me))
    n = len(games)
    print()
    if not n:
        print("No ranked games in the cache for this player yet.")
        return
    wins = sum(me["win"] for _, me in games)
    mins = [info["gameDuration"] / 60 for info, _ in games]
    k = sum(me["kills"] for _, me in games)
    d = sum(me["deaths"] for _, me in games)
    a = sum(me["assists"] for _, me in games)
    cs = [(me["totalMinionsKilled"] + me["neutralMinionsKilled"]) / t
          for (info, me), t in zip(games, mins)]
    vis = [me["visionScore"] / t for (info, me), t in zip(games, mins)]

    print("RAW STATS · last %d %s games (%d remakes excluded, %d without "
          "timeline)" % (n, label, remakes, no_tl))
    print("  Win rate     %s   (%dW %dL, ± = 90%% Wilson)" % (
        fmt_wr(wins, n), wins, n - wins))
    print("  KDA          %.2f   (%.1f / %.1f / %.1f)" % (
        (k + a) / max(d, 1), k / n, d / n, a / n))
    print("  CS/min       %.2f" % (sum(cs) / n))
    print("  Vision/min   %.2f" % (sum(vis) / n))
    print("  Avg length   %s" % fmt_dur(sum(mins) / n * 60))

    roles = Counter(me.get("teamPosition", "") for _, me in games)
    role_w = Counter(me.get("teamPosition", "") for _, me in games
                     if me["win"])
    print("\nROLES")
    for r, g in roles.most_common():
        print("  %-8s %4d games  %5.1f%%   WR %s" % (
            ROLE_LABEL.get(r, r), g, 100 * g / n, fmt_wr(role_w[r], g)))

    champs = defaultdict(list)
    for (info, me), t in zip(games, mins):
        champs[me["championName"]].append((info, me, t))
    print("\nCHAMPIONS (top 10)")
    print("  %-14s %5s  %-15s %6s %7s" % ("Champion", "Games", "WR", "KDA",
                                          "CS/min"))
    for name, rows in sorted(champs.items(), key=lambda kv: -len(kv[1]))[:10]:
        g = len(rows)
        w = sum(me["win"] for _, me, _ in rows)
        kk = sum(me["kills"] + me["assists"] for _, me, _ in rows)
        dd = sum(me["deaths"] for _, me, _ in rows)
        c = sum((me["totalMinionsKilled"] + me["neutralMinionsKilled"]) / t
                for _, me, t in rows) / g
        print("  %-14s %5d  %-15s %6.2f %7.2f" % (
            name, g, fmt_wr(w, g), kk / max(dd, 1), c))

    print("\nLATEST 8 GAMES")
    for info, me in games[:8]:
        when = datetime.fromtimestamp(info["gameCreation"] / 1000)
        t = info["gameDuration"] / 60
        print("  %s  %-12s %-7s %s  %2d/%2d/%2d  %5.1f cs/m  %s  %s" % (
            when.strftime("%Y-%m-%d %H:%M"), me["championName"],
            ROLE_LABEL.get(me.get("teamPosition", ""), "?"),
            "W" if me["win"] else "L", me["kills"], me["deaths"],
            me["assists"],
            (me["totalMinionsKilled"] + me["neutralMinionsKilled"]) / t,
            fmt_dur(info["gameDuration"]),
            "patch " + ".".join(info.get("gameVersion", "?").split(".")[:2])))
    print("\nDone · %d games · %s · %s" % (n, label, region))


# ---------------------------------------------------------------- commands

def cmd_fetch(args) -> int:
    region = get_region(args.region)
    name, tag = parse_riot_id(args.riot_id)
    queues = QUEUES[args.queue]
    label = " + ".join(QUEUE_LABEL[q] for q in queues)
    with Cache(config.db_path()) as cache:
        if args.offline:
            puuid = cache.find_player(name, tag, region.code)
            if not puuid:
                print("%s#%s isn't in the cache yet — run without --offline "
                      "first." % (name, tag))
                return 1
            ids = cache.player_match_ids(puuid, queues, args.count)
            summarize(cache, puuid, ids, label, region.code)
            return 0

        client = default_client()
        job = SyncJob(client, cache, args.riot_id, region, queues=queues,
                      count=args.count, workers=args.workers,
                      on_progress=ProgressPrinter())
        job.start()
        try:
            while job._thread and job._thread.is_alive():
                job.join(0.2)
        except KeyboardInterrupt:
            print("\nCancelling… (everything fetched so far is kept)")
            job.cancel()
            job.join()
        if job.error:
            raise job.error
        res = job.result
        print()
        print("%s · %s" % (res.riot_id, res.region))
        solo = [e for e in res.rank_entries
                if e.get("queueType") in ("RANKED_SOLO_5x5", "RANKED_FLEX_SR")]
        for e in solo:
            q = "Solo/Duo" if e["queueType"] == "RANKED_SOLO_5x5" else "Flex"
            print("  %-9s %s" % (q, fmt_rank(e)))
        if not solo:
            print("  Unranked this split")
        print("Games: %d listed · %d already cached · %d matches + %d "
              "timelines fetched%s" % (
                  len(res.match_ids), res.already_cached, res.matches_fetched,
                  res.timelines_fetched,
                  " · %d timelines unavailable" % res.timelines_missing
                  if res.timelines_missing else ""))
        print("Requests: %d in %s (%.2f req/s)" % (
            res.requests, fmt_dur(res.seconds),
            res.requests / max(res.seconds, 1e-9)))
        for mid, err in res.errors[:5]:
            print("  ! %s: %s" % (mid, err))
        if res.cancelled:
            print("Cancelled — partial data kept; re-run to continue.")
        summarize(cache, res.puuid, res.match_ids, label, res.region)
    return 0


def cmd_analyze(args) -> int:
    from . import static
    from .analysis import ROLES, Filter
    from .pipeline import analyze_player
    from .report import render

    region = get_region(args.region)
    name, tag = parse_riot_id(args.riot_id)
    role = None
    if args.role:
        aliases = {"BOT": "BOTTOM", "ADC": "BOTTOM", "MID": "MIDDLE",
                   "SUPPORT": "UTILITY", "SUP": "UTILITY", "JG": "JUNGLE"}
        role = aliases.get(args.role.upper(), args.role.upper())
        if role not in ROLES:
            raise ValueError("role must be one of top/jungle/mid/bot/support")
    with Cache(config.db_path()) as cache:
        puuid = cache.find_player(name, tag, region.code)
        if not puuid:
            print("%s#%s isn't in the cache yet — run `fetch` first."
                  % (name, tag))
            return 1
        flt = Filter(queues=QUEUES[args.queue], role=role,
                     champion=args.champion, last_n=args.last)
        res = analyze_player(cache, puuid, flt, static.legendary_ids(),
                             use_cache=not args.no_cache)
        hist = cache.rank_history(puuid)
        rank = ""
        if hist:
            h = hist[-1]
            rank = fmt_rank({"tier": h.tier, "rank": h.division,
                             "leaguePoints": h.lp, "wins": h.wins,
                             "losses": h.losses}).split("  (")[0]
        if not res.n_games:
            print("No games match that filter.")
            return 1
        print(render(res, "%s#%s" % (name, tag), region.code, rank))
    return 0


def cmd_cache(args) -> int:
    path = config.db_path()
    with Cache(path) as cache:
        s = cache.stats()
    size = path.stat().st_size / 1e6 if path.exists() else 0
    print("Cache: %s (%.1f MB)" % (path, size))
    for k, v in s.items():
        print("  %-15s %d" % (k, v))
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="review",
                                 description="REVIEW — ranked account analyzer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="fetch a player's ranked games")
    f.add_argument("riot_id", help='Riot ID, e.g. "flintgnuggaren#ian"')
    f.add_argument("--region", default="EUW", type=str.upper,
                   help="%s (also: %s)" % (", ".join(PRIMARY_REGIONS),
                                           ", ".join(r for r in REGIONS
                                                     if r not in PRIMARY_REGIONS)))
    f.add_argument("--queue", default="solo", choices=list(QUEUES))
    f.add_argument("--count", type=int, default=DEFAULT_GAMES,
                   help="number of most recent games (default 150)")
    f.add_argument("--workers", type=int, default=4)
    f.add_argument("--offline", action="store_true",
                   help="use the cache only, no API calls")
    f.set_defaults(func=cmd_fetch)
    a = sub.add_parser("analyze", help="analyze cached games (no API calls)")
    a.add_argument("riot_id")
    a.add_argument("--region", default="EUW", type=str.upper)
    a.add_argument("--queue", default="solo", choices=list(QUEUES))
    a.add_argument("--role", help="top/jungle/mid/bot/support")
    a.add_argument("--champion", help="e.g. Aphelios (Riot's internal name)")
    a.add_argument("--last", type=int, help="only the last N games")
    a.add_argument("--no-cache", action="store_true",
                   help="recompute instead of using the cached result")
    a.set_defaults(func=cmd_analyze)
    c = sub.add_parser("cache", help="show cache location and size")
    c.set_defaults(func=cmd_cache)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ReviewError, ValueError) as e:
        print("✖ %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
