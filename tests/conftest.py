import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeClock:
    """Deterministic time: sleep() just advances the clock."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make_match(match_id, puuid_list, creation=0, duration=1800, queue=420,
               winners=(100,)):
    parts = []
    for i, pu in enumerate(puuid_list):
        team = 100 if i < 5 else 200
        parts.append({
            "puuid": pu, "teamId": team,
            "teamPosition": ["TOP", "JUNGLE", "MIDDLE", "BOTTOM",
                             "UTILITY"][i % 5],
            "championId": i + 1, "championName": "Champ%d" % i,
            "win": team in winners, "kills": 3, "deaths": 2, "assists": 5,
            "totalMinionsKilled": 150, "neutralMinionsKilled": 10,
            "visionScore": 20,
        })
    return {"metadata": {"matchId": match_id,
                         "participants": list(puuid_list)},
            "info": {"platformId": "EUW1", "queueId": queue,
                     "gameCreation": creation, "gameDuration": duration,
                     "gameVersion": "16.22.123.4567",
                     "participants": parts}}
