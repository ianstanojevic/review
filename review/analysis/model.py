"""Plain data model produced by extraction and consumed by the engine.

Everything here is JSON-serialisable (NaN allowed) so extracted features can
be cached and the engine stays a pure function of them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_LABEL = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid",
              "BOTTOM": "Bot", "UTILITY": "Support"}
NAN = float("nan")


@dataclass
class Death:
    t: float                    # minutes
    x: int
    y: int
    zone: str                   # see zones.py
    solo: bool                  # single enemy champion, no assists
    while_ahead: bool           # ≥ +300 gold vs lane opponent at the time
    before_enemy_objective: bool  # enemy took a monster/tower within 60 s
    enemy_half: bool
    killer_champion: str | None = None


@dataclass
class PlayerGame:
    puuid: str
    pid: int                    # participantId 1..10
    team: int                   # 100 blue / 200 red
    role: str | None            # one of ROLES, None if Riot couldn't tell
    champion: str
    champion_id: int
    win: bool
    riot_id: str
    opponent_pid: int | None
    opponent_champion: str | None
    stats: dict[str, float]
    curves: dict[str, list[float]] = field(default_factory=dict)
    deaths: list[Death] = field(default_factory=list)
    dmg_mix: tuple[float, float, float] = (NAN, NAN, NAN)  # phys/magic/true


@dataclass
class MatchFeatures:
    match_id: str
    queue_id: int
    map_id: int
    creation_ms: int
    end_ms: int
    duration_s: int
    patch: str
    remake: bool
    has_timeline: bool
    players: list[PlayerGame]
    version: str = ""

    def player(self, puuid: str) -> PlayerGame | None:
        return next((p for p in self.players if p.puuid == puuid), None)

    def by_pid(self, pid: int) -> PlayerGame | None:
        return next((p for p in self.players if p.pid == pid), None)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "MatchFeatures":
        players = []
        for p in d["players"]:
            p = dict(p)
            p["deaths"] = [Death(**x) for x in p.get("deaths", [])]
            p["dmg_mix"] = tuple(p.get("dmg_mix", (NAN, NAN, NAN)))
            players.append(PlayerGame(**p))
        d = dict(d, players=players)
        return MatchFeatures(**d)
