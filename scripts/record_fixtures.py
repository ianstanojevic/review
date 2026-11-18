"""Record anonymised match + timeline fixtures from the local cache.

    python scripts/record_fixtures.py "flintgnuggaren#ian" EUW

Picks a Bot game, a Support game and a remake for the player, replaces every
PUUID / Riot ID / summoner identifier with stable placeholders (the player
becomes "puuid-me"), and writes gzip JSON to tests/fixtures/. Fixtures can
be committed without publishing anyone's account.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from review import config  # noqa: E402
from review.cache import Cache  # noqa: E402
from review.riot_client import parse_riot_id  # noqa: E402

OUT = ROOT / "tests" / "fixtures"
SCRUB = ("summonerId", "summonerName", "profileIcon", "summonerLevel")


def anonymise(match: dict, timeline: dict | None, me: str):
    ids = {me: "puuid-me"}
    for p in match["info"]["participants"]:
        ids.setdefault(p["puuid"], "puuid-%d" % p["participantId"])
    for p in match["info"]["participants"]:
        n = p["participantId"]
        p["puuid"] = ids[p["puuid"]]
        p["riotIdGameName"] = "Me" if p["puuid"] == "puuid-me" else "Player%d" % n
        p["riotIdTagline"] = "TST"
        for k in SCRUB:
            p.pop(k, None)
    match["metadata"]["participants"] = [ids.get(x, "puuid-x")
                                         for x in match["metadata"]["participants"]]
    if timeline:
        timeline["metadata"]["participants"] = [
            ids.get(x, "puuid-x") for x in timeline["metadata"]["participants"]]
        for tp in timeline["info"].get("participants", []):
            tp["puuid"] = ids.get(tp["puuid"], "puuid-x")
    return match, timeline


def main(riot_id: str, region: str) -> None:
    name, tag = parse_riot_id(riot_id)
    OUT.mkdir(parents=True, exist_ok=True)
    with Cache(config.db_path()) as c:
        me = c.find_player(name, tag, region)
        ids = c.player_match_ids(me, [420])
        want = {"bot": None, "support": None, "remake": None}
        for mid in ids:
            m = c.get_match(mid)
            info = m["info"]
            p = next(x for x in info["participants"] if x["puuid"] == me)
            remake = info["gameDuration"] < 300 or any(
                x.get("gameEndedInEarlySurrender") for x in info["participants"])
            if remake and not want["remake"]:
                want["remake"] = mid
            elif not remake and p["teamPosition"] == "BOTTOM" and not want["bot"]:
                want["bot"] = mid
            elif not remake and p["teamPosition"] == "UTILITY" and not want["support"]:
                want["support"] = mid
        for label, mid in want.items():
            if not mid:
                print("no %s game found" % label)
                continue
            m, t = anonymise(c.get_match(mid), c.get_timeline(mid), me)
            m["metadata"]["matchId"] = "TEST1_%s" % label.upper()
            path = OUT / ("%s.json.gz" % label)
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump({"match": m, "timeline": t}, f)
            print("wrote", path.relative_to(ROOT), "from", mid)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "EUW")
