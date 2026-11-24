from conftest import make_match
from review.cache import Cache

PUUIDS = ["p%d" % i for i in range(10)]


def test_roundtrip_and_participants(tmp_path):
    with Cache(tmp_path / "c.sqlite") as c:
        m = make_match("EUW1_1", PUUIDS, creation=1)
        c.put_match(m)
        assert c.get_match("EUW1_1") == m
        assert c.missing_matches(["EUW1_1", "EUW1_2"]) == {"EUW1_2"}
        # every participant can find the shared match
        assert c.player_match_ids("p7") == ["EUW1_1"]


def test_player_matches_newest_first_and_queue_filter(tmp_path):
    with Cache(tmp_path / "c.sqlite") as c:
        c.put_match(make_match("EUW1_1", PUUIDS, creation=100))
        c.put_match(make_match("EUW1_3", PUUIDS, creation=300))
        c.put_match(make_match("EUW1_2", PUUIDS, creation=200, queue=440))
        assert c.player_match_ids("p0") == ["EUW1_3", "EUW1_2", "EUW1_1"]
        assert c.player_match_ids("p0", [420]) == ["EUW1_3", "EUW1_1"]
        assert c.player_match_ids("p0", [420], limit=1) == ["EUW1_3"]


def test_timeline_missing_is_remembered(tmp_path):
    with Cache(tmp_path / "c.sqlite") as c:
        c.put_timeline("EUW1_1", None)
        c.put_timeline("EUW1_2", {"frames": []})
        assert c.get_timeline("EUW1_1") is None
        assert c.get_timeline("EUW1_2") == {"frames": []}
        assert c.missing_timelines(["EUW1_1", "EUW1_2", "EUW1_3"]) == {"EUW1_3"}


def test_persists_across_connections(tmp_path):
    p = tmp_path / "c.sqlite"
    with Cache(p) as c:
        c.put_match(make_match("EUW1_1", PUUIDS))
        c.upsert_player("p0", "flintgnuggaren", "ian", "EUW")
    with Cache(p) as c:
        assert c.get_match("EUW1_1") is not None
        assert c.find_player("FlintGnuggaren", "IAN", "EUW") == "p0"


def test_rank_snapshots(tmp_path):
    with Cache(tmp_path / "c.sqlite") as c:
        e = {"queueType": "RANKED_SOLO_5x5", "tier": "EMERALD", "rank": "II",
             "leaguePoints": 45, "wins": 10, "losses": 8}
        c.add_rank_snapshot("p0", e, taken_at=1)
        c.add_rank_snapshot("p0", dict(e, leaguePoints=62), taken_at=2)
        h = c.rank_history("p0")
        assert [s.lp for s in h] == [45, 62]
        assert c.rank_history("p0", "RANKED_FLEX_SR") == []


def test_raw_timeline_bytes_roundtrip(tmp_path):
    import json
    with Cache(tmp_path / "c.sqlite") as c:
        body = json.dumps({"info": {"frames": [{"timestamp": 0}]}}).encode()
        c.put_timeline("EUW1_9", body)
        assert c.get_timeline("EUW1_9") == {"info": {"frames": [
            {"timestamp": 0}]}}
        assert not c.missing_timelines(["EUW1_9"])


def test_player_region(tmp_path):
    from review.cache import Cache
    with Cache(tmp_path / "c.sqlite") as c:
        c.upsert_player("p1", "Doublelift", "NA01", "NA")
        assert c.player_region("p1") == "NA"
        assert c.player_region("nobody") is None
