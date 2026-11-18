"""Benchmarks: a fixed "gold standard" built from real games of
higher-ranked players, so scores mean the same thing on every account.

A benchmark is a set of recent ranked Solo/Duo games played by a random
sample of players on a ladder level (Diamond, or Master + Grandmaster +
Challenger) in one region. Every participant of those games is (roughly)
that level, so all ten players feed the role pools: 300 games give ~600
samples per role. Nothing is invented — it is the same percentile method
as the default baseline, just against a different, shared pool.

Games are cached like any other match (fetched once, shared), and the
benchmark membership lives in the ``benchmark_games`` table.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

from .cache import Cache
from .riot_client import Cancelled, NotFound, RiotApiError, RiotClient
from .riot_client.routing import Region
from .sync import Progress, SyncJob, SyncResult

SOLO = 420
DEFAULT_GAMES = 300
MIN_USABLE = 100          # below this a benchmark isn't offered for use
PER_PLAYER = 4            # recent games taken from each sampled player


@dataclass(frozen=True)
class Level:
    key: str
    label: str            # shown in the UI ("Master+")
    blurb: str


LEVELS = {                      # low -> high (UI order)
    "gold": Level("gold", "Gold", "Gold I–IV players"),
    "platinum": Level("platinum", "Platinum", "Platinum I–IV players"),
    "emerald": Level("emerald", "Emerald", "Emerald I–IV players"),
    "diamond": Level("diamond", "Diamond",
                     "Diamond I–IV players"),
    "master": Level("master", "Master+",
                    "Master, Grandmaster and Challenger players"),
}


def estimate_minutes(games: int, client_rate: float = 100 / 120) -> float:
    """~2 requests per game (match + timeline) + one id list per player."""
    req = games * 2 + games / PER_PLAYER * 1.3 + 8
    return req / client_rate / 60


class BenchmarkJob(SyncJob):
    """Background build of one benchmark (reuses SyncJob's parallel,
    rate-limited, cancellable downloader)."""

    def __init__(self, client: RiotClient, cache: Cache, region: Region,
                 level: str, *, games: int = DEFAULT_GAMES, workers: int = 2,
                 on_progress: Callable[[Progress], None] | None = None,
                 seed: int | None = None):
        if level not in LEVELS:
            raise ValueError("unknown benchmark level %r" % level)
        self.client, self.cache, self.region = client, cache, region
        self.level = level
        self.games = max(MIN_USABLE, int(games))
        self.workers = max(1, workers)
        self._on_progress = on_progress or (lambda p: None)
        self.seed = seed
        import threading
        self._cancel = threading.Event()
        self._thread = None
        self.result: SyncResult | None = None
        self.error: BaseException | None = None
        self.puuid = self.riot_id = None
        self.ids: list[str] = []

    # -- ladder ---------------------------------------------------------
    def _ladder(self) -> list[str]:
        c, reg, ev = self.client, self.region, self._cancel
        out: list[str] = []
        if self.level == "master":
            for tier in ("challenger", "grandmaster", "master"):
                out += [e["puuid"] for e in c.apex_league(reg, tier,
                                                          cancel=ev)
                        if e.get("puuid") and not e.get("inactive")]
        else:
            for div in ("I", "II", "III", "IV"):
                out += [e["puuid"] for e in c.league_page(
                    reg, self.level.upper(), div, 1, cancel=ev)
                    if e.get("puuid") and not e.get("inactive")]
        return out

    def run(self) -> SyncResult:
        t0 = time.monotonic()
        req0 = self.client.requests_sent
        reg, lv = self.region, LEVELS[self.level]
        res = SyncResult(riot_id=lv.label, region=reg.code, puuid="",
                         queues=(SOLO,))
        have = set(self.cache.benchmark_ids(reg.code, self.level))
        try:
            ids: list[str] = list(have)
            if len(ids) < self.games:
                self._emit(phase="ids", message="Reading the %s ladder on "
                           "%s…" % (lv.label, reg.code))
                players = self._ladder()
                rng = random.Random(self.seed if self.seed is not None
                                    else time.time_ns())
                rng.shuffle(players)
                seen = set(ids)
                for i, pu in enumerate(players):
                    if len(seen) >= self.games:
                        break
                    if self._cancel.is_set():
                        raise Cancelled()
                    self._emit(phase="ids", done=len(seen), total=self.games,
                               message="Sampling %s players… %d/%d games"
                               % (lv.label, len(seen), self.games))
                    try:
                        page = self.client.match_ids(
                            reg, pu, queue=SOLO, count=PER_PLAYER,
                            cancel=self._cancel)
                    except NotFound:
                        continue
                    for m in page:
                        if m not in seen and len(seen) < self.games:
                            seen.add(m)
                            ids.append(m)
            res.match_ids = ids
            self.ids = ids
            need_match = self.cache.missing_matches(ids)
            need_tl = self.cache.missing_timelines(ids)
            todo = [m for m in ids if m in need_match or m in need_tl]
            res.already_cached = len(ids) - len(todo)
            self._fetch_all(reg, todo, need_match, need_tl, res)
        except Cancelled:
            res.cancelled = True
        except RiotApiError:
            # keep whatever was fetched; the caller shows the error
            self.cache.add_benchmark(reg.code, self.level,
                                     [m for m in res.match_ids])
            raise
        # membership only for games that made it into the cache (the join
        # in benchmark_ids ignores the rest anyway)
        self.cache.add_benchmark(reg.code, self.level, res.match_ids)
        res.requests = self.client.requests_sent - req0
        res.seconds = time.monotonic() - t0
        self._emit(phase="done", done=len(res.match_ids),
                   total=len(res.match_ids),
                   message="Cancelled" if res.cancelled else "Done")
        return res
