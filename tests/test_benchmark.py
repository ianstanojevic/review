"""Benchmarks: the fetch job (fake Riot client) and scoring against a
benchmark instead of the player's own games."""
import numpy as np
import pytest

from analysis_helpers import ME, synth_games
from review.analysis import Filter, analyze, load_config
from review.analysis.metrics import METRICS
from review.benchmark import MIN_USABLE, BenchmarkJob
from review.cache import Cache
from test_sync import EUW, FakeClient


class LadderClient(FakeClient):
    """Ladder of 60 players, each with 4 recent games of their own."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.players = ["pl%d" % i for i in range(60)]
        self.ladder_calls = 0

    def apex_league(self, region, tier, queue="RANKED_SOLO_5x5", cancel=None):
        self._tick(cancel)
        self.ladder_calls += 1
        share = {"challenger": self.players[:5],
                 "grandmaster": self.players[5:15],
                 "master": self.players[15:]}[tier]
        return [{"puuid": p, "inactive": False} for p in share]

    def league_page(self, region, tier, division, page=1, cancel=None,
                    queue="RANKED_SOLO_5x5"):
        self._tick(cancel)
        i = "I II III IV".split().index(division)
        return [{"puuid": p} for p in self.players[i * 15:(i + 1) * 15]]

    def match_ids(self, region, puuid, *, queue, start=0, count=100,
                  start_time=None, cancel=None):
        self._tick(cancel)
        self.calls["ids"] += 1
        k = int(puuid[2:])
        return ["EUW1_%d" % (5000 + k * 10 + j) for j in range(count)]


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "c.sqlite") as c:
        yield c


@pytest.mark.parametrize("level", ["master", "diamond"])
def test_build_benchmark(cache, level):
    fc = LadderClient()
    res = BenchmarkJob(fc, cache, EUW, level, games=120, seed=1).run()
    assert len(res.match_ids) == 120
    assert res.matches_fetched == 120
    assert fc.calls["ids"] == 30            # 4 games per sampled player
    n, t = cache.benchmark_info("EUW", level)
    assert n == 120 and t > 0
    assert len(cache.benchmark_ids("EUW", level)) == 120
    # other level / region untouched
    other = "diamond" if level == "master" else "master"
    assert cache.benchmark_info("EUW", other)[0] == 0
    assert cache.benchmark_info("NA", level)[0] == 0


def test_fetch_more_reuses_cached_games(cache):
    fc = LadderClient()
    BenchmarkJob(fc, cache, EUW, "master", games=100, seed=1).run()
    first = len(fc.calls["match"])
    res = BenchmarkJob(fc, cache, EUW, "master", games=160, seed=2).run()
    assert cache.benchmark_info("EUW", "master")[0] == 160
    # only the 60 new games were downloaded
    assert len(fc.calls["match"]) - first == 60
    assert res.already_cached >= 100


def test_cancelled_build_keeps_what_was_fetched(cache):
    fc = LadderClient(delay=0.02)
    job = BenchmarkJob(fc, cache, EUW, "master", games=200, seed=1,
                       workers=2)
    job.start()
    import time
    deadline = time.monotonic() + 20
    while len(fc.calls["match"]) < 5 and time.monotonic() < deadline:
        time.sleep(0.02)     # wait until downloads are under way
    job.cancel()
    job.join(5)
    n, _ = cache.benchmark_info("EUW", "master")
    assert job.result.cancelled
    assert 0 < n < 200
    assert MIN_USABLE == 100


def _stronger(games, sds=1.0):
    """Every participant better by ``sds`` peer SDs (a 'higher league')."""
    for m in games:
        m.match_id = "REF_" + m.match_id
        for p in m.players:
            p.puuid = "ref-" + p.puuid
            for k, met in METRICS.items():
                if met.polarity and not met.zero_sum:
                    p.stats[k] += sds * 2 * met.polarity
    return games


def test_benchmark_puts_accounts_on_one_scale():
    cfg = load_config()
    ref = _stronger(synth_games(120, seed=9), sds=1.0)
    avg = synth_games(150, boost=0.0, seed=1)
    good = synth_games(150, boost=1.0, seed=2)
    # own-games baseline: both look like their own lobbies (~50 vs higher)
    own_avg = analyze(avg, ME, Filter(), cfg, compute_ci=False).radar
    assert 40 < own_avg.overall < 60
    # against the stronger benchmark: the average player falls below 50,
    # the player who is as good as the benchmark sits near 50
    r_avg = analyze(avg, ME, Filter(), cfg, compute_ci=False,
                    reference=ref, reference_label="Master+")
    r_good = analyze(good, ME, Filter(), cfg, compute_ci=False,
                     reference=ref, reference_label="Master+")
    assert r_avg.reference_label == "Master+"
    assert r_avg.reference_games == 120
    assert r_avg.radar.overall < 42
    assert r_good.radar.overall > r_avg.radar.overall + 8
    assert abs(r_good.radar.overall - 50) < 10
    # findings speak about the benchmark, not "peers"
    texts = " ".join(f.text for f in r_avg.weaknesses + r_avg.strengths)
    assert "peers" not in texts
    assert "Master+ players" in texts


def test_reference_excludes_remakes_and_other_queues():
    cfg = load_config()
    ref = _stronger(synth_games(60, seed=4))
    for m in ref[:10]:
        m.remake = True
    for m in ref[10:20]:
        m.queue_id = 450                     # ARAM
    r = analyze(synth_games(40, seed=5), ME, Filter(), cfg,
                compute_ci=False, reference=ref, reference_label="Diamond")
    assert r.reference_games == 40
    assert np.isfinite(r.radar.overall)
