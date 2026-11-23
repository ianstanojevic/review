"""One game from your point of view: who you played, who you laned
against, and every moment worth jumping to in the replay (your deaths with
their context, your kills, objectives, towers). Built from the match and
timeline JSON that Review already caches."""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import fmt_time

OBJECTIVE_WINDOW_MS = 60_000
REMAKE_SECONDS = 300      # same rule as REVIEW (review.analysis.extract)


def is_remake(info: dict) -> bool:
    """A remake: ended early (surrender vote before 3-4 min) or so short
    that it can't be a real game. Its W/L means nothing."""
    return (info.get("gameDuration", 0) < REMAKE_SECONDS
            or any(p.get("gameEndedInEarlySurrender")
                   for p in info.get("participants", [])))
EPIC = {"DRAGON": "Dragon", "BARON_NASHOR": "Baron", "RIFTHERALD": "Herald",
        "HORDE": "Grubs", "ATAKHAN": "Atakhan", "ELDER_DRAGON": "Elder"}


@dataclass
class Moment:
    t_ms: int
    kind: str              # death | kill | assist | objective | tower
    label: str
    x: float | None = None
    y: float | None = None
    ours: bool = True      # objective/tower: taken by your team
    detail: dict = field(default_factory=dict)

    @property
    def time_text(self) -> str:
        return fmt_time(self.t_ms / 1000)


@dataclass
class Player:
    pid: int
    puuid: str
    champion: str
    role: str | None
    team: int
    riot_id: str = ""


@dataclass
class GameView:
    match_id: str
    game_id: int
    puuid: str
    me: Player
    players: dict[int, Player]
    win: bool
    kills: int
    deaths: int
    assists: int
    cs: int
    duration_s: int
    created_ms: int
    version: str
    queue: int
    opponent: Player | None = None
    ally_support: Player | None = None
    enemy_support: Player | None = None
    moments: list[Moment] = field(default_factory=list)
    has_timeline: bool = True
    remake: bool = False

    @property
    def patch(self) -> str:
        p = self.version.split(".")
        return ".".join(p[:2]) if len(p) >= 2 else self.version

    @property
    def deaths_list(self) -> list[Moment]:
        return [m for m in self.moments if m.kind == "death"]

    @property
    def kda_text(self) -> str:
        return "%d / %d / %d" % (self.kills, self.deaths, self.assists)

    def champ(self, pid: int | None) -> str | None:
        p = self.players.get(pid) if pid else None
        return p.champion if p else None


ROLE_FROM_POS = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MIDDLE",
                 "BOTTOM": "BOTTOM", "UTILITY": "UTILITY"}


def _riot_id(p: dict) -> str:
    n, t = p.get("riotIdGameName"), p.get("riotIdTagline")
    return "%s#%s" % (n, t) if n and t else (n or p.get("summonerName") or "")


def build_game(match: dict, timeline: dict | None, puuid: str) -> GameView:
    info = match["info"]
    parts = info["participants"]
    players: dict[int, Player] = {}
    me_raw = None
    for p in parts:
        pid = p.get("participantId")
        players[pid] = Player(pid, p.get("puuid", ""), p.get("championName",
                                                              "?"),
                              ROLE_FROM_POS.get(p.get("teamPosition") or ""),
                              p.get("teamId", 0), _riot_id(p))
        if p.get("puuid") == puuid:
            me_raw = p
    if me_raw is None:
        raise ValueError("player not in match %s" % match["metadata"]
                         ["matchId"])
    me = players[me_raw["participantId"]]

    def find(role, team_same: bool):
        c = [q for q in players.values() if q.role == role and q.pid != me.pid
             and (q.team == me.team) == team_same]
        return c[0] if len(c) == 1 else None

    opp = find(me.role, False) if me.role else None
    if me.role in ("BOTTOM", "UTILITY"):
        ally_sup = find("UTILITY", True) if me.role == "BOTTOM" else None
        enemy_sup = find("UTILITY", False)
        if me.role == "UTILITY":            # as support: the lane is the ADC
            ally_sup = find("BOTTOM", True)
            enemy_sup = find("BOTTOM", False)
    else:
        ally_sup = enemy_sup = None

    mid = match["metadata"]["matchId"]
    try:
        game_id = int(info.get("gameId") or mid.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        game_id = 0
    g = GameView(
        match_id=mid, game_id=game_id, puuid=puuid, me=me, players=players,
        win=bool(me_raw.get("win")), kills=me_raw.get("kills", 0),
        deaths=me_raw.get("deaths", 0), assists=me_raw.get("assists", 0),
        cs=me_raw.get("totalMinionsKilled", 0)
        + me_raw.get("neutralMinionsKilled", 0),
        duration_s=info.get("gameDuration", 0),
        created_ms=info.get("gameCreation", 0),
        version=info.get("gameVersion", ""), queue=info.get("queueId", 0),
        opponent=opp, ally_support=ally_sup, enemy_support=enemy_sup,
        remake=is_remake(info))
    if timeline:
        g.moments = _moments(g, timeline)
    else:
        g.has_timeline = False
    return g


def _frames(timeline: dict) -> list[dict]:
    return (timeline.get("info") or timeline).get("frames", [])


def _gold(frames, t_ms, pid) -> int | None:
    i = int(t_ms // 60_000)
    if 0 <= i < len(frames):
        f = frames[i].get("participantFrames", {}).get(str(pid))
        if f:
            return f.get("totalGold")
    return None


def _level(frames, t_ms, pid) -> int | None:
    i = int(t_ms // 60_000)
    if 0 <= i < len(frames):
        f = frames[i].get("participantFrames", {}).get(str(pid))
        if f:
            return f.get("level")
    return None


def _moments(g: GameView, timeline: dict) -> list[Moment]:
    frames = _frames(timeline)
    events = sorted((e for f in frames for e in f.get("events", [])),
                    key=lambda e: e.get("timestamp", 0))
    me, team = g.me.pid, g.me.team
    enemy_obj_times = [
        e["timestamp"] for e in events
        if (e["type"] == "ELITE_MONSTER_KILL"
            and e.get("killerTeamId") not in (None, team))
        or (e["type"] == "BUILDING_KILL" and e.get("teamId") == team)]
    out: list[Moment] = []
    for e in events:
        t, typ = e.get("timestamp", 0), e["type"]
        pos = e.get("position") or {}
        x, y = pos.get("x"), pos.get("y")
        if typ == "CHAMPION_KILL":
            killer = e.get("killerId", 0)
            assists = e.get("assistingParticipantIds", []) or []
            if e.get("victimId") == me:
                out.append(_death(g, frames, e, enemy_obj_times))
            elif killer == me:
                out.append(Moment(t, "kill", "Killed %s" % (
                    g.champ(e.get("victimId")) or "?"), x, y,
                    detail=_kill_detail(frames, e, me)))
            elif me in assists:
                out.append(Moment(t, "assist", "Assist on %s" % (
                    g.champ(e.get("victimId")) or "?"), x, y,
                    detail=_kill_detail(frames, e, me)))
        elif typ == "ELITE_MONSTER_KILL":
            name = EPIC.get(e.get("monsterSubType") or "") or EPIC.get(
                e.get("monsterType") or "") or "Monster"
            if e.get("monsterType") == "DRAGON" and e.get("monsterSubType"):
                sub = e["monsterSubType"].replace("_DRAGON", "").title()
                name = "Elder" if sub == "Elder" else "%s dragon" % sub
            ours = e.get("killerTeamId") == team
            out.append(Moment(t, "objective", "%s %s" % (
                "Your team took" if ours else "Enemy took", name), x, y,
                ours=ours, detail=_took(e, me) | {"name": name}))
        elif typ == "BUILDING_KILL":
            ours = e.get("teamId") != team        # teamId = building owner
            what = "Inhibitor" if e.get("buildingType") == "INHIBITOR_BUILDING" \
                else "%s tower" % (e.get("laneType", "") or "").replace(
                    "_LANE", "").title()
            out.append(Moment(t, "tower", "%s: %s" % (
                "Destroyed" if ours else "Lost", what.strip()), x, y,
                ours=ours, detail=_took(e, me) | {"name": what.strip()}))
    return out


def _kill_detail(frames, e, me) -> dict:
    """A kill you made or helped with: who, with whom, what it was worth
    and how far behind (or ahead) of the victim you were in gold."""
    killer = e.get("killerId", 0)
    victim = e.get("victimId", 0)
    a, b = _gold(frames, e.get("timestamp", 0), me), _gold(
        frames, e.get("timestamp", 0), victim)
    return {"killer": killer, "victim": victim,
            "assists": [p for p in (e.get("assistingParticipantIds") or [])
                        if p],
            "bounty": e.get("bounty") or 0,
            "shutdown": e.get("shutdownBounty") or 0,
            "gold_diff": a - b if a is not None and b is not None else None}


def _took(e, me) -> dict:
    """Objective/tower: whether you took part (last hit or assist)."""
    killer = e.get("killerId", 0)
    assists = [p for p in (e.get("assistingParticipantIds") or []) if p]
    return {"killer": killer, "assists": assists,
            "mine": killer == me or me in assists}


def _death(g: GameView, frames, e, enemy_obj_times) -> Moment:
    t = e.get("timestamp", 0)
    pos = e.get("position") or {}
    killer = e.get("killerId", 0)
    assists = [a for a in (e.get("assistingParticipantIds") or []) if a]
    involved = ([killer] if 1 <= killer <= 10 else []) + assists
    jungler = any(g.players.get(p) and g.players[p].role == "JUNGLE"
                  for p in involved)
    gd = None
    if g.opponent:
        a, b = _gold(frames, t, g.me.pid), _gold(frames, t, g.opponent.pid)
        if a is not None and b is not None:
            gd = a - b
    followed = [u for u in enemy_obj_times if t < u <= t + OBJECTIVE_WINDOW_MS]
    killer_name = g.champ(killer) if 1 <= killer <= 10 else (
        "Tower" if killer == 0 else "Minion/monster")
    detail = {
        "killer": killer_name,
        "assists": [g.champ(a) for a in assists],
        "enemies": len(involved),
        "solo": 1 <= killer <= 10 and not assists,
        "jungler": jungler,
        "gold_diff": gd,
        "level": _level(frames, t, g.me.pid),
        "objective_after": bool(followed),
        "bounty": e.get("shutdownBounty") or 0,
    }
    lab = "Killed by %s" % killer_name
    if assists:
        lab += " +%d" % len(assists)
    return Moment(t, "death", lab, pos.get("x"), pos.get("y"), detail=detail)


def context_lines(m: Moment) -> list[str]:
    """Plain-language facts about a death, for the tag panel."""
    d = m.detail
    out = []
    n = d.get("enemies", 0)
    out.append("Solo death" if d.get("solo") else
               "%d enemies involved" % n if n else "No champion involved")
    if d.get("assists"):
        out.append("Assists: %s" % ", ".join(a for a in d["assists"] if a))
    if d.get("jungler"):
        out.append("Enemy jungler involved")
    gd = d.get("gold_diff")
    if gd is not None:
        out.append("Gold vs lane opponent: %+d" % gd)
    if d.get("level"):
        out.append("Your level: %d" % d["level"])
    if d.get("objective_after"):
        out.append("Enemy took an objective within 60 s")
    if d.get("bounty"):
        out.append("Gave a %dg shutdown" % d["bounty"])
    return out
