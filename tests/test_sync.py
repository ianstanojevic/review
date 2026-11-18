import threading
import time

import pytest

from conftest import make_match
from review.cache import Cache
from review.riot_client import (InvalidKey, NotFound, PlayerNotFound,
                                RateLimiter, get_region)
from review.sync import SyncJob

EUW = get_region("EUW")
ME = "me"
OTHERS = ["o%d" % i for i in range(9)]


class FakeClient:
    """Stands in for RiotClient; counts calls per endpoint."""

    def __init__(self, n_games=30, no_timeline=(), bad_key_on=None,
                 delay=0.0):
        self.limiter = RateLimiter([(100, 120.0)])
        self.requests_sent = 0
        self.ids = ["EUW1_%d" % (1000 + i) for i in range(n_games)][::-1]
        self.no_timeline = set(no_timeline)
        self.bad_key_on = bad_key_on
        self.delay = delay
        self.calls = {"match": [], "timeline": [], "ids": 0}

    def _tick(self, cancel):
        self.requests_sent += 1
        if self.delay:
            if cancel is not None and cancel.wait(self.delay):
                from review.riot_client import Cancelled
                raise Cancelled()

    def account_by_riot_id(self, region, name, tag, cancel=None):
        self._tick(cancel)
        if name == "ghost":
            raise NotFound()
        return {"puuid": ME, "gameName": name, "tagLine": tag}

    def league_entries(self, region, puuid, cancel=None):
        self._tick(cancel)
        return [{"queueType": "RANKED_SOLO_5x5", "tier": "EMERALD",
                 "rank": "II", "leaguePoints": 45, "wins": 1, "losses": 1}]

    def match_ids(self, region, puuid, *, queue, start=0, count=100,
                  start_time=None, cancel=None):
        self._tick(cancel)
        self.calls["ids"] += 1
        return self.ids[start:start + count]

    def match(self, region, mid, cancel=None):
        self._tick(cancel)
        if mid == self.bad_key_on:
            raise InvalidKey(403)
        self.calls["match"].append(mid)
        return make_match(mid, [ME] + OTHERS, creation=int(mid.split("_")[1]))

    def timeline(self, region, mid, cancel=None, raw=False):
        self._tick(cancel)
        self.calls["timeline"].append(mid)
        if mid in self.no_timeline:
            raise NotFound()
        return {"info": {"frames": []}}


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "c.sqlite") as c:
        yield c


def test_full_then_incremental(cache):
    fc = FakeClient(n_games=30, no_timeline={"EUW1_1005"})
    seen = []
    res = SyncJob(fc, cache, "flint#ian", EUW, count=25,
                  on_progress=seen.append).run()
    assert len(res.match_ids) == 25
    assert res.match_ids[0] == "EUW1_1029"          # newest first
    assert res.matches_fetched == 25 and res.timelines_fetched == 24
    assert res.timelines_missing == 1              # 1005 is in the top 25
    assert seen[-1].phase == "done"
    assert any(p.message == "Fetching 25/25 games…" for p in seen)
    assert cache.rank_history(ME)[0].lp == 45

    # second run: nothing new -> zero match/timeline downloads
    fc.calls = {"match": [], "timeline": [], "ids": 0}
    res2 = SyncJob(fc, cache, "flint#ian", EUW, count=25).run()
    assert fc.calls["match"] == [] and fc.calls["timeline"] == []
    assert res2.already_cached == 25

    # two new games appear -> only those are fetched
    fc.ids = ["EUW1_2001", "EUW1_2000"] + fc.ids
    res3 = SyncJob(fc, cache, "flint#ian", EUW, count=25).run()
    assert sorted(fc.calls["match"]) == ["EUW1_2000", "EUW1_2001"]


def test_missing_timeline_recorded_not_refetched(cache):
    fc = FakeClient(n_games=5, no_timeline={"EUW1_1002"})
    res = SyncJob(fc, cache, "a#b", EUW, count=5).run()
    assert res.timelines_missing == 1
    fc.calls["timeline"] = []
    SyncJob(fc, cache, "a#b", EUW, count=5).run()
    assert fc.calls["timeline"] == []


def test_paging_over_100(cache):
    fc = FakeClient(n_games=250)
    res = SyncJob(fc, cache, "a#b", EUW, count=230).run()
    assert len(res.match_ids) == 230
    assert fc.calls["ids"] == 3


def test_shared_matches_between_friends(cache):
    fc = FakeClient(n_games=10)
    SyncJob(fc, cache, "a#b", EUW, count=10).run()
    # a friend who was in all those games: no downloads at all
    assert cache.player_match_ids("o3") and len(cache.player_match_ids("o3")) == 10


def test_unknown_player(cache):
    with pytest.raises(PlayerNotFound):
        SyncJob(FakeClient(), cache, "ghost#x", EUW).run()


def test_invalid_key_aborts(cache):
    fc = FakeClient(n_games=20, bad_key_on="EUW1_1010")
    with pytest.raises(InvalidKey):
        SyncJob(fc, cache, "a#b", EUW, count=20, workers=1).run()


def test_cancel_keeps_partial(cache):
    fc = FakeClient(n_games=200, delay=0.01)
    job = SyncJob(fc, cache, "a#b", EUW, count=200, workers=2)
    job.start()
    time.sleep(0.3)
    job.cancel()
    job.join(5)
    assert job.error is None
    res = job.result
    assert res.cancelled
    assert 0 < res.matches_fetched < 200
    assert len(cache.player_match_ids(ME)) == res.matches_fetched
