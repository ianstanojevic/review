"""Background job: bring a player's ranked games into the cache.

Steps: Riot ID -> PUUID, rank snapshot, list the latest N match IDs, then
download only what the cache lacks (match and/or timeline), newest first so
partial results are the most recent games. Progress is reported through a
callback (called from the job's thread — the UI must marshal it), and the
job can be cancelled at any time; everything fetched so far stays cached.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

from .cache import Cache
from .riot_client import (Cancelled, InvalidKey, NotFound, PlayerNotFound,
                          Region, RiotApiError, RiotClient, parse_riot_id)

QUEUES = {"solo": (420,), "flex": (440,), "both": (420, 440)}
QUEUE_LABEL = {420: "Solo/Duo", 440: "Flex"}
RANKED_QUEUE_TYPE = {420: "RANKED_SOLO_5x5", 440: "RANKED_FLEX_SR"}
MIN_GAMES, MAX_GAMES, DEFAULT_GAMES = 1, 1000, 150


@dataclass
class Progress:
    phase: str             # account | rank | ids | games | done
    done: int = 0
    total: int = 0
    message: str = ""
    eta_s: float | None = None
    new_match_id: str | None = None


@dataclass
class SyncResult:
    riot_id: str
    region: str
    puuid: str
    queues: tuple[int, ...]
    match_ids: list[str] = field(default_factory=list)  # requested, newest first
    rank_entries: list[dict] = field(default_factory=list)
    already_cached: int = 0
    matches_fetched: int = 0
    timelines_fetched: int = 0
    timelines_missing: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    requests: int = 0
    seconds: float = 0.0


def _id_number(match_id: str) -> int:
    try:
        return int(match_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


class SyncJob:
    def __init__(self, client: RiotClient, cache: Cache, riot_id: str,
                 region: Region, *, queues: tuple[int, ...] = (420,),
                 count: int = DEFAULT_GAMES, workers: int = 2,
                 on_progress: Callable[[Progress], None] | None = None):
        self.client, self.cache, self.region = client, cache, region
        self.game_name, self.tag = parse_riot_id(riot_id)
        self.queues = tuple(queues)
        self.count = max(MIN_GAMES, min(MAX_GAMES, int(count)))
        self.workers = max(1, workers)
        self._on_progress = on_progress or (lambda p: None)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.result: SyncResult | None = None
        self.error: BaseException | None = None
        # known as soon as the account is resolved (for partial results)
        self.puuid: str | None = None
        self.riot_id: str | None = None
        self.ids: list[str] = []

    # -- control --------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def start(self) -> threading.Thread:
        """Run in a daemon thread; read ``result`` / ``error`` afterwards."""
        def target():
            try:
                self.result = self.run()
            except BaseException as e:  # surfaced to the UI via .error
                self.error = e
        self._thread = threading.Thread(target=target, daemon=True,
                                        name="review-sync")
        self._thread.start()
        return self._thread

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # -- work -----------------------------------------------------------
    def _emit(self, **kw) -> None:
        try:
            self._on_progress(Progress(**kw))
        except Exception:
            pass   # a broken progress listener must never kill the job

    def _eta(self, requests_left: int) -> float:
        return requests_left / self.client.limiter.sustained_rate()

    def run(self) -> SyncResult:
        t0 = time.monotonic()
        req0 = self.client.requests_sent
        c, ev, reg = self.client, self._cancel, self.region

        self._emit(phase="account", message="Looking up %s#%s on %s…"
                   % (self.game_name, self.tag, reg.code))
        try:
            acct = c.account_by_riot_id(reg, self.game_name, self.tag,
                                        cancel=ev)
        except NotFound:
            raise PlayerNotFound("No player called %s#%s on %s."
                                 % (self.game_name, self.tag, reg.code))
        puuid = acct["puuid"]
        name = "%s#%s" % (acct.get("gameName") or self.game_name,
                          acct.get("tagLine") or self.tag)
        self.cache.upsert_player(puuid, acct.get("gameName") or self.game_name,
                                 acct.get("tagLine") or self.tag, reg.code)
        res = SyncResult(riot_id=name, region=reg.code, puuid=puuid,
                         queues=self.queues)
        self.puuid, self.riot_id = puuid, name

        try:
            self._emit(phase="rank", message="Reading rank…")
            res.rank_entries = c.league_entries(reg, puuid, cancel=ev)
            now = int(time.time())
            for e in res.rank_entries:
                if e.get("queueType") in RANKED_QUEUE_TYPE.values():
                    self.cache.add_rank_snapshot(puuid, e, now)

            self._emit(phase="ids", message="Listing ranked games…")
            res.match_ids = self._collect_ids(reg, puuid)
            self.ids = res.match_ids

            need_match = self.cache.missing_matches(res.match_ids)
            need_tl = self.cache.missing_timelines(res.match_ids)
            todo = [m for m in res.match_ids if m in need_match or m in need_tl]
            res.already_cached = len(res.match_ids) - len(todo)
            self._fetch_all(reg, todo, need_match, need_tl, res)
        except Cancelled:
            res.cancelled = True

        res.requests = c.requests_sent - req0
        res.seconds = time.monotonic() - t0
        self._emit(phase="done", done=len(res.match_ids),
                   total=len(res.match_ids),
                   message="Cancelled" if res.cancelled else "Done")
        return res

    def _collect_ids(self, reg: Region, puuid: str) -> list[str]:
        ids: set[str] = set()
        for q in self.queues:
            got: list[str] = []
            while len(got) < self.count:
                n = min(100, self.count - len(got))
                page = self.client.match_ids(reg, puuid, queue=q,
                                             start=len(got), count=n,
                                             cancel=self._cancel)
                got += page
                if len(page) < n:
                    break
            ids.update(got)
        return sorted(ids, key=_id_number, reverse=True)[:self.count]

    def _fetch_one(self, reg: Region, mid: str, want_match: bool,
                   want_tl: bool):
        match = timeline = None
        tl_missing = False
        if want_match:
            match = self.client.match(reg, mid, cancel=self._cancel)
        if want_tl:
            try:
                timeline = self.client.timeline(reg, mid, cancel=self._cancel,
                                                raw=True)
            except NotFound:
                tl_missing = True
        return mid, match, timeline, tl_missing

    def _fetch_all(self, reg, todo, need_match, need_tl, res: SyncResult):
        total = len(todo)
        if not total:
            self._emit(phase="games", done=0, total=0,
                       message="All %d games already cached"
                       % len(res.match_ids))
            return
        left_requests = sum((m in need_match) + (m in need_tl) for m in todo)
        self._emit(phase="games", done=0, total=total,
                   eta_s=self._eta(left_requests),
                   message="Fetching 0/%d games…" % total)
        done = consecutive_fail = 0
        pending = {}
        queue = iter(todo)
        pool = ThreadPoolExecutor(self.workers, thread_name_prefix="riot")
        try:
            def submit_next():
                mid = next(queue, None)
                if mid is not None:
                    f = pool.submit(self._fetch_one, reg, mid,
                                    mid in need_match, mid in need_tl)
                    pending[f] = mid
            for _ in range(self.workers * 2):
                submit_next()
            while pending:
                finished, _ = wait(pending, timeout=0.5,
                                   return_when=FIRST_COMPLETED)
                if self._cancel.is_set():
                    raise Cancelled()
                for f in finished:
                    mid = pending.pop(f)
                    left_requests -= (mid in need_match) + (mid in need_tl)
                    try:
                        _, match, tl, tl_missing = f.result()
                    except (Cancelled, InvalidKey):
                        raise
                    except NotFound:
                        res.errors.append((mid, "match not found"))
                    except RiotApiError as e:
                        res.errors.append((mid, str(e)))
                        consecutive_fail += 1
                        if consecutive_fail >= 8:
                            raise e   # API is down — stop hammering it
                    else:
                        consecutive_fail = 0
                        if match is not None:
                            self.cache.put_match(match)
                            res.matches_fetched += 1
                        if tl is not None:
                            self.cache.put_timeline(mid, tl)
                            res.timelines_fetched += 1
                        elif tl_missing:
                            self.cache.put_timeline(mid, None)
                            res.timelines_missing += 1
                    done += 1
                    self._emit(phase="games", done=done, total=total,
                               eta_s=self._eta(max(left_requests, 0)),
                               new_match_id=mid,
                               message="Fetching %d/%d games…" % (done, total))
                    submit_next()
        except BaseException:
            # stop in-flight workers (they may be waiting on the limiter)
            self._cancel.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
