import math

import numpy as np
import pytest

from analysis_helpers import ME, load_fixture, synth_games, variant
from review.analysis import Filter, analyze, extract_match, load_config
from review.analysis.peers import PeerBaseline


@pytest.fixture(scope="module")
def cfg():
    c = load_config()
    c.iterations = 300          # keep tests fast; behaviour is the same
    return c


# ------------------------------------------------------- real fixture games

@pytest.fixture(scope="module")
def fixture_matches():
    bot_m, bot_t = load_fixture("bot")
    sup_m, sup_t = load_fixture("support")
    rem_m, rem_t = load_fixture("remake")
    aram = variant(bot_m, "TEST1_ARAM", queueId=450, mapId=12)
    flex = variant(bot_m, "TEST1_FLEX", queueId=440)
    no_tl = variant(sup_m, "TEST1_NOTL", gameCreation=sup_m["info"]["gameCreation"] + 1)
    return [extract_match(bot_m, bot_t), extract_match(sup_m, sup_t),
            extract_match(rem_m, rem_t), extract_match(aram, bot_t),
            extract_match(flex, bot_t), extract_match(no_tl, None)]


def test_exclusions(fixture_matches, cfg):
    r = analyze(fixture_matches, ME, Filter(), cfg)
    # solo queue only: bot + support + no-timeline support
    assert r.n_games == 3
    assert r.excluded.remakes == 1
    assert r.excluded.other_queues == 2        # ARAM + flex
    assert r.excluded.no_timeline == 1
    assert r.radar is not None and r.radar.insufficient   # 3 < min_games
    both = analyze(fixture_matches, ME, Filter(queues=(420, 440)), cfg)
    assert both.n_games == 4


def test_role_filter(fixture_matches, cfg):
    r = analyze(fixture_matches, ME, Filter(role="UTILITY"), cfg)
    assert r.n_games == 2
    assert all(g.role == "UTILITY" for g in r.games)


def test_no_games_is_not_an_error(fixture_matches, cfg):
    r = analyze(fixture_matches, ME, Filter(champion="NotAChampion"), cfg)
    assert r.n_games == 0 and r.radar is None and r.strengths == []


# ------------------------------------------------------------ synthetic games

def test_deterministic(cfg):
    games = synth_games(40, boost=0.3)
    a = analyze(games, ME, Filter(), cfg).radar
    b = analyze(games, ME, Filter(), cfg).radar
    assert [x.score for x in a.axes] == [x.score for x in b.axes]
    assert a.overall_lo == b.overall_lo


def test_average_player_scores_about_50(cfg):
    r = analyze(synth_games(150, boost=0.0, seed=1), ME, Filter(), cfg).radar
    assert 44 < r.overall < 56
    assert r.overall_lo < 50 < r.overall_hi or abs(r.overall - 50) < 4
    for a in r.axes[:-1]:
        assert 35 < a.score < 65, a


def test_better_player_scores_higher_with_tight_ci(cfg):
    r = analyze(synth_games(150, boost=0.5, seed=2), ME, Filter(), cfg).radar
    assert r.overall > 58
    fighting = r.axis("fighting")
    assert fighting.lo < fighting.score < fighting.hi
    assert fighting.hi - fighting.lo < 15


def test_shrinkage_on_small_samples(cfg):
    """Six great games must not show as a 98."""
    big = analyze(synth_games(150, boost=1.5, seed=3), ME, Filter(), cfg).radar
    small = analyze(synth_games(6, boost=1.5, seed=3), ME, Filter(), cfg).radar
    f_big, f_small = big.axis("fighting"), small.axis("fighting")
    assert f_small.score < f_small.raw          # pulled toward 50
    assert f_small.score < f_big.score
    assert f_small.score < 85
    assert (f_small.hi - f_small.lo) > (f_big.hi - f_big.lo)


def test_peer_pool_excludes_self_and_mirrors_zero_sum():
    games = synth_games(30, seed=4)
    b = PeerBaseline(games, ME)
    assert b.n("BOTTOM") == 30 * 1 and b.n("TOP") == 30 * 2
    # zero-sum stat: direct opponents removed, then mirrored -> mean 0
    v = b.values("BOTTOM", "gold_diff_15")
    assert len(v) == 0                          # every bot peer was my opponent
    v = b.values("TOP", "gold_diff_15")
    assert len(v) == 120 and abs(v.mean()) < 1e-9
    assert b.exact_mean("gold_diff_15") and not b.exact_mean("cs_per_min")


def test_findings_need_enough_games_and_signal(cfg):
    good = analyze(synth_games(150, boost=0.6, seed=5), ME, Filter(), cfg)
    assert good.strengths and not [w for w in good.weaknesses
                                   if not w.key.startswith("champion:")]
    for f in good.strengths:
        assert f.n >= 20 and f.confidence in ("high", "medium") and f.tip
    few = analyze(synth_games(12, boost=0.6, seed=5), ME, Filter(), cfg)
    assert not [f for f in few.strengths if not f.key.startswith("champion:")]


def test_bad_early_game_is_a_weakness(cfg):
    games = synth_games(120, seed=6)
    for m in games:
        me = m.player(ME)
        me.stats["deaths_pre14"] += 4
    r = analyze(games, ME, Filter(), cfg)
    keys = [w.key for w in r.weaknesses]
    assert "deaths_pre14" in keys
    w = r.weaknesses[keys.index("deaths_pre14")]
    assert "n=120" in w.text


def test_trend_verdicts(cfg):
    up = analyze(synth_games(120, seed=7, trend={"cs_per_min": 4.0}), ME,
                 Filter(), cfg)
    t = next(t for t in up.trends if t.key == "cs_per_min")
    assert t.verdict == "improving" and t.change > 0
    flat = analyze(synth_games(120, seed=8), ME, Filter(), cfg)
    t = next(t for t in flat.trends if t.key == "cs_per_min")
    assert t.verdict == "stable"
    down = analyze(synth_games(120, seed=9, trend={"deaths_per_min": 4.0}),
                   ME, Filter(), cfg)
    t = next(t for t in down.trends if t.key == "deaths_per_min")
    assert t.verdict == "declining"            # more deaths = worse


def test_wins_vs_losses_ranks_the_driver(cfg):
    games = synth_games(150, seed=10)
    rng = np.random.default_rng(10)
    for m in games:
        me = m.player(ME)
        me.stats["gold_diff_15"] = (1500.0 if me.win else -1500.0) + rng.normal(0, 600)
    r = analyze(games, ME, Filter(), cfg)
    assert r.deciders[0].key == "gold_diff_15" and r.deciders[0].effect.d > 2


def test_sessions_and_tilt_inputs(cfg):
    games = synth_games(20, seed=11)
    r = analyze(games, ME, Filter(), cfg)
    # synth games are 3 h apart -> every game is its own session
    assert r.sessions.n_sessions == 20
    assert r.sessions.by_game_number[0].rate.n == 20


def test_trend_sees_a_recent_turn(cfg):
    """Down then up (a U): no straight-line trend, but the newest third is
    clearly worse than the middle one, and the verdict must say so."""
    games = synth_games(120, seed=12)
    games.sort(key=lambda m: m.creation_ms)
    rng = np.random.default_rng(12)
    for i, m in enumerate(games):
        u = abs(i - 60) / 60                       # 1 -> 0 -> 1
        m.player(ME).stats["deaths_per_min"] = 0.1 + 0.15 * u + \
            rng.normal(0, 0.02)
    t = next(t for t in analyze(games, ME, Filter(), cfg).trends
             if t.key == "deaths_per_min")
    assert t.verdict == "stable" and t.recent == "declining"
    flat = analyze(synth_games(120, seed=8), ME, Filter(), cfg)
    assert all(t.recent is None for t in flat.trends)


def test_no_scoreable_games_gives_no_radar(cfg):
    games = synth_games(30, seed=13)
    for m in games:
        m.player(ME).role = None          # Riot gave no position
    r = analyze(games, ME, Filter(), cfg)
    assert r.n_games == 30 and r.radar is None
    assert r.excluded.unknown_role == 30


def test_empty_reference_falls_back_to_own_games(cfg):
    games = synth_games(40, seed=14)
    r = analyze(games, ME, Filter(), cfg, reference=[],
                reference_label="Master+")
    assert r.reference_label is None and r.reference_games == 0
    assert r.radar is not None and r.radar.overall == r.radar.overall


def test_every_stat_has_a_plain_explanation():
    from review.analysis.metrics import HELP, METRICS
    assert set(METRICS) <= set(HELP)
    assert all(len(HELP[k]) < 140 for k in METRICS)
