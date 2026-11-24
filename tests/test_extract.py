import math

import pytest

from analysis_helpers import ME, load_fixture, variant
from review.analysis.extract import extract_match, is_remake
from review.analysis.zones import in_enemy_half, relative_zone, zone


@pytest.fixture(scope="module")
def bot():
    return load_fixture("bot")


def test_player_role_and_opponent(bot):
    m, t = bot
    f = extract_match(m, t)
    me = f.player(ME)
    assert me.role == "BOTTOM"
    opp = f.by_pid(me.opponent_pid)
    assert opp.role == "BOTTOM" and opp.team != me.team
    assert me.opponent_champion == opp.champion
    assert f.has_timeline and not f.remake and f.patch.count(".") == 1


def test_lane_diffs_match_frames(bot):
    m, t = bot
    f = extract_match(m, t)
    me = f.player(ME)
    fr = t["info"]["frames"][10]["participantFrames"]
    a, b = fr[str(me.pid)], fr[str(me.opponent_pid)]
    assert me.stats["gold_diff_10"] == a["totalGold"] - b["totalGold"]
    assert me.stats["xp_diff_10"] == a["xp"] - b["xp"]
    cs = lambda x: x["minionsKilled"] + x["jungleMinionsKilled"]
    assert me.stats["cs_diff_10"] == cs(a) - cs(b)
    # zero-sum: the opponent sees exactly the mirror image
    opp = f.by_pid(me.opponent_pid)
    assert opp.stats["gold_diff_15"] == -me.stats["gold_diff_15"]
    assert me.curves["gold"][10] == me.stats["gold_diff_10"]


def test_deaths_match_scoreboard(bot):
    m, t = bot
    f = extract_match(m, t)
    for p in f.players:
        assert len(p.deaths) == p.stats["deaths"]
        assert p.stats["deaths_pre14"] == sum(d.t < 14 for d in p.deaths)
        for d in p.deaths:
            assert d.zone == zone(d.x, d.y)


def test_basic_rates(bot):
    m, t = bot
    f = extract_match(m, t)
    me = f.player(ME)
    raw = next(p for p in m["info"]["participants"] if p["puuid"] == ME)
    mins = m["info"]["gameDuration"] / 60
    assert me.stats["cs_per_min"] == pytest.approx(
        (raw["totalMinionsKilled"] + raw["neutralMinionsKilled"]) / mins)
    team_dmg = sum(p["totalDamageDealtToChampions"]
                   for p in m["info"]["participants"]
                   if p["teamId"] == raw["teamId"])
    assert me.stats["dmg_share"] == pytest.approx(
        raw["totalDamageDealtToChampions"] / team_dmg)
    assert 0 <= me.stats["kp"] <= 1
    assert sum(me.dmg_mix) == pytest.approx(1.0)


def test_missing_timeline(bot):
    m, _ = bot
    f = extract_match(m, None)
    me = f.player(ME)
    assert not f.has_timeline
    for k in ("gold_diff_15", "deaths_pre14", "epic_part", "turret_plates",
              "first_legendary_min"):
        assert math.isnan(me.stats[k])
    assert not math.isnan(me.stats["cs_per_min"])      # match-only stats stay
    assert me.deaths == [] and me.curves == {}


def test_remake_detected():
    m, t = load_fixture("remake")
    assert is_remake(m["info"])
    assert extract_match(m, t).remake


def test_role_swap_falls_back_to_individual_position(bot):
    m, t = bot
    m2 = variant(m, "TEST1_SWAP")
    me = next(p for p in m2["info"]["participants"] if p["puuid"] == ME)
    me["teamPosition"] = ""
    me["individualPosition"] = "UTILITY"
    f = extract_match(m2, t)
    p = f.player(ME)
    assert p.role == "UTILITY"
    # a teammate is also UTILITY now; still exactly one enemy UTILITY
    assert p.opponent_pid is not None


def test_unknown_role(bot):
    m, t = bot
    m2 = variant(m, "TEST1_NOROLE")
    me = next(p for p in m2["info"]["participants"] if p["puuid"] == ME)
    me["teamPosition"] = ""
    me["individualPosition"] = "Invalid"
    p = extract_match(m2, t).player(ME)
    assert p.role is None and p.opponent_pid is None
    assert math.isnan(p.stats["gold_diff_15"])


def test_first_legendary(bot):
    m, t = bot
    me = next(p for p in m["info"]["participants"] if p["puuid"] == ME)
    buys = [e for f in t["info"]["frames"] for e in f["events"]
            if e["type"] == "ITEM_PURCHASED"
            and e["participantId"] == me["participantId"]]
    target = buys[-1]["itemId"]
    first = min(e["timestamp"] for e in buys if e["itemId"] == target)
    p = extract_match(m, t, legendary={target}).player(ME)
    assert p.stats["first_legendary_min"] == pytest.approx(first / 60000, abs=0.01)


def test_features_roundtrip(bot):
    from review.analysis.model import MatchFeatures
    m, t = bot
    f = extract_match(m, t)
    g = MatchFeatures.from_dict(f.to_dict())
    assert g.player(ME).deaths == f.player(ME).deaths
    assert g.player(ME).stats["kda"] == f.player(ME).stats["kda"]


def test_zones():
    assert zone(500, 500) == "blue_base"
    assert zone(14300, 14300) == "red_base"
    assert zone(1000, 9000) == "top_lane"
    assert zone(9000, 1000) == "bot_lane"
    assert zone(7400, 7400) == "mid_lane"
    assert zone(9500, 5300) == "river_bot"
    assert zone(5300, 9500) == "river_top"
    assert zone(4000, 7000) == "blue_jungle_top"
    assert relative_zone("red_jungle_bot", 100) == "enemy_jungle_bot"
    assert relative_zone("red_jungle_bot", 200) == "own_jungle_bot"
    assert in_enemy_half(10000, 10000, 100) and not in_enemy_half(10000, 10000, 200)


def test_early_afk_surrender_counts_as_remake():
    from review.analysis.extract import is_remake
    ps = [{"gameEndedInSurrender": True, "gameEndedInEarlySurrender": False}]
    assert is_remake({"gameDuration": 309, "participants": ps})
    assert not is_remake({"gameDuration": 1500, "participants": ps})
    assert is_remake({"gameDuration": 95, "participants": []})
    assert not is_remake({"gameDuration": 1500, "participants": [{}]})
