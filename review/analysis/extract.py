"""Per-game feature extraction from raw match-v5 + timeline JSON.

The SAME function runs for all ten participants, so the player and the peer
baseline are measured identically. Timeline-only stats are NaN when Riot has
no timeline for the game.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from .model import NAN, ROLES, Death, MatchFeatures, PlayerGame
from .zones import in_enemy_half, zone

FEATURE_VERSION = "4"
REMAKE_SECONDS = 300
EARLY_MS = 14 * 60_000
AHEAD_GOLD = 300           # "ahead" = ≥ this much gold over lane opponent
OBJECTIVE_WINDOW_MS = 60_000
CURVE_MINUTES = 60          # whole game (lane charts use 0–20)
FIGHT_SIZE = 3             # killer + assists ≥ 3 champions = a fight
EPIC_MONSTERS = {"DRAGON", "RIFTHERALD", "HORDE", "BARON_NASHOR", "ATAKHAN"}


def _role(p: dict) -> str | None:
    for k in ("teamPosition", "individualPosition"):
        r = p.get(k) or ""
        if r in ROLES:
            return r
    return None


def _div(a: float, b: float) -> float:
    return a / b if b else NAN


def _patch(version: str) -> str:
    parts = (version or "").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else "?"


AFK_SURRENDER_SECONDS = 600


def is_remake(info: dict) -> bool:
    """Remakes, and surrenders before 10:00 (only possible when a teammate
    left): a 5-minute AFK game's per-minute stats are noise (1.9 CS/min),
    so like remakes they're left out of the analysis."""
    d = info.get("gameDuration", 0)
    ps = info.get("participants", [])
    return (d < REMAKE_SECONDS
            or any(p.get("gameEndedInEarlySurrender") for p in ps)
            or (d < AFK_SURRENDER_SECONDS
                and any(p.get("gameEndedInSurrender") for p in ps)))


class _Timeline:
    """Index over a timeline: frames by minute, events by type."""

    def __init__(self, tl: dict):
        info = tl.get("info", tl)
        self.frames = info.get("frames", [])
        self.events = [e for f in self.frames for e in f.get("events", [])]
        self.events.sort(key=lambda e: e.get("timestamp", 0))
        self.kills = [e for e in self.events if e["type"] == "CHAMPION_KILL"]
        self.elite = [e for e in self.events
                      if e["type"] == "ELITE_MONSTER_KILL"]
        self.buildings = [e for e in self.events
                          if e["type"] == "BUILDING_KILL"]
        self.plates = [e for e in self.events
                       if e["type"] == "TURRET_PLATE_DESTROYED"]
        self.purchases = defaultdict(list)    # pid -> [(t, itemId)]
        undo = defaultdict(list)
        for e in self.events:
            if e["type"] == "ITEM_PURCHASED":
                self.purchases[e.get("participantId")].append(
                    (e["timestamp"], e["itemId"]))
            elif e["type"] == "ITEM_UNDO":
                undo[e.get("participantId")].append(
                    (e["timestamp"], e.get("beforeId")))
        for pid, undos in undo.items():           # drop undone purchases
            buys = self.purchases[pid]
            for t, item in undos:
                for i in range(len(buys) - 1, -1, -1):
                    if buys[i][1] == item and buys[i][0] <= t:
                        del buys[i]
                        break

    def pf(self, minute: int, pid: int) -> dict | None:
        if minute < len(self.frames):
            return self.frames[minute]["participantFrames"].get(str(pid))
        return None

    def gold_at(self, t_ms: float, pid: int) -> float | None:
        f = self.pf(int(t_ms // 60_000), pid)
        return f["totalGold"] if f else None


def _cs(f: dict) -> int:
    return f.get("minionsKilled", 0) + f.get("jungleMinionsKilled", 0)


def _involved(e: dict, pid: int) -> bool:
    return e.get("killerId") == pid or pid in e.get("assistingParticipantIds", [])


def extract_match(match: dict, timeline: dict | None = None,
                  legendary: Iterable[int] = ()) -> MatchFeatures:
    info = match["info"]
    parts = info["participants"]
    legendary = frozenset(legendary)
    dur_s = info.get("gameDuration", 0)
    dur = max(dur_s / 60.0, 1e-9)
    tl = _Timeline(timeline) if timeline else None

    by_pid = {p["participantId"]: p for p in parts}
    roles = {pid: _role(p) for pid, p in by_pid.items()}
    opponent: dict[int, int | None] = {}
    for pid, p in by_pid.items():
        cands = [q for q, r in roles.items()
                 if r and r == roles[pid] and by_pid[q]["teamId"] != p["teamId"]]
        opponent[pid] = cands[0] if roles[pid] and len(cands) == 1 else None

    team_tot = defaultdict(lambda: defaultdict(float))
    for p in parts:
        t = team_tot[p["teamId"]]
        t["kills"] += p.get("kills", 0)
        t["dmg"] += p.get("totalDamageDealtToChampions", 0)
        t["taken"] += p.get("totalDamageTaken", 0)
        t["cs"] += p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
    epic_by_team = {}
    for t in info.get("teams", []):
        o = t.get("objectives", {})
        epic_by_team[t["teamId"]] = sum(
            o.get(k, {}).get("kills", 0)
            for k in ("dragon", "riftHerald", "horde", "baron", "atakhan"))
    epic_total = sum(epic_by_team.values())

    players = []
    for pid, p in sorted(by_pid.items()):
        team = p["teamId"]
        opp = opponent[pid]
        k, d, a = p.get("kills", 0), p.get("deaths", 0), p.get("assists", 0)
        ch = p.get("challenges") or {}
        cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
        dmg = p.get("totalDamageDealtToChampions", 0)
        tt = team_tot[team]
        s: dict[str, float] = {
            "kills": k, "deaths": d, "assists": a,
            "kda": (k + a) / max(d, 1),
            "kp": _div(k + a, tt["kills"]),
            "cs": cs,
            "cs_per_min": cs / dur,
            "cs_share": _div(cs, tt["cs"]),
            "gold_per_min": p.get("goldEarned", 0) / dur,
            "dmg_per_min": dmg / dur,
            "dmg_share": _div(dmg, tt["dmg"]),
            "dmg_per_gold": _div(dmg, p.get("goldEarned", 0)),
            "dmg_taken_share": _div(p.get("totalDamageTaken", 0), tt["taken"]),
            "mitigated_per_death": p.get("damageSelfMitigated", 0) / max(d, 1),
            "vision_per_min": p.get("visionScore", 0) / dur,
            "wards_placed_per_min": p.get("wardsPlaced", 0) / dur,
            "wards_killed_per_min": p.get("wardsKilled", 0) / dur,
            "control_wards": p.get("visionWardsBoughtInGame", 0),
            "solo_kills": ch.get("soloKills", NAN),
            "turret_dmg_per_min": p.get("damageDealtToTurrets", 0) / dur,
            "cc_per_min": p.get("timeCCingOthers", 0) / dur,
            "ally_support_per_min": (p.get("totalHealsOnTeammates", 0)
                                     + p.get("totalDamageShieldedOnTeammates", 0)) / dur,
            "team_obj_control": _div(epic_by_team.get(team, 0), epic_total),
            "fb_involved": float(bool(p.get("firstBloodKill")
                                      or p.get("firstBloodAssist"))),
            "deaths_per_min": d / dur,
            "win": float(bool(p.get("win"))),
        }
        for key in ("cs_diff_10", "cs_diff_15", "gold_diff_10", "gold_diff_15",
                    "xp_diff_10", "xp_diff_15", "kills_pre14", "deaths_pre14",
                    "takedowns_pre14", "early_death", "solo_deaths",
                    "early_dmg", "early_towers", "fight_ka_per_death",
                    "deaths_ahead_rate", "deaths_before_enemy_obj",
                    "obj_part", "epic_part", "first_legendary_min",
                    "turret_plates"):
            s[key] = NAN
        curves: dict[str, list[float]] = {}
        deaths: list[Death] = []

        if tl is not None:
            _timeline_stats(tl, pid, team, opp, by_pid, s, curves, deaths,
                            legendary)

        mix = (p.get("physicalDamageDealtToChampions", 0),
               p.get("magicDamageDealtToChampions", 0),
               p.get("trueDamageDealtToChampions", 0))
        tot = sum(mix)
        players.append(PlayerGame(
            puuid=p.get("puuid", ""), pid=pid, team=team, role=roles[pid],
            champion=p.get("championName", "?"),
            champion_id=p.get("championId", 0), win=bool(p.get("win")),
            riot_id="%s#%s" % (p.get("riotIdGameName", "?"),
                               p.get("riotIdTagline", "?")),
            opponent_pid=opp,
            opponent_champion=by_pid[opp].get("championName") if opp else None,
            stats=s, curves=curves, deaths=deaths,
            dmg_mix=tuple(x / tot for x in mix) if tot else (NAN, NAN, NAN)))

    end_ms = info.get("gameEndTimestamp") or (
        info.get("gameCreation", 0) + dur_s * 1000)
    return MatchFeatures(
        match_id=match["metadata"]["matchId"], queue_id=info.get("queueId", 0),
        map_id=info.get("mapId", 0), creation_ms=info.get("gameCreation", 0),
        end_ms=end_ms, duration_s=dur_s, patch=_patch(info.get("gameVersion")),
        remake=is_remake(info), has_timeline=tl is not None, players=players,
        version=FEATURE_VERSION)


def _timeline_stats(tl: _Timeline, pid, team, opp, by_pid, s, curves, deaths,
                    legendary):
    # lane diffs vs the direct opponent, per minute and @10/@15
    if opp is not None:
        g, c, x = [], [], []
        for m in range(CURVE_MINUTES + 1):
            a, b = tl.pf(m, pid), tl.pf(m, opp)
            if not a or not b:
                break
            g.append(a["totalGold"] - b["totalGold"])
            c.append(_cs(a) - _cs(b))
            x.append(a["xp"] - b["xp"])
        curves.update(gold=g, cs=c, xp=x)
        for m in (10, 15):
            if len(g) > m:
                s["gold_diff_%d" % m] = g[m]
                s["cs_diff_%d" % m] = c[m]
                s["xp_diff_%d" % m] = x[m]

    f14 = tl.pf(14, pid)
    if f14:
        s["early_dmg"] = f14.get("damageStats", {}).get(
            "totalDamageDoneToChampions", NAN)

    kills_pre = deaths_pre = assists_pre = 0
    solo_deaths = ahead_deaths = obj_deaths = 0
    fight_ta = fight_deaths = 0
    enemy_obj_times = [e["timestamp"] for e in tl.elite
                       if e.get("killerTeamId") != team]
    enemy_obj_times += [e["timestamp"] for e in tl.buildings
                        if e.get("teamId") == team]   # our building fell
    for e in tl.kills:
        t = e["timestamp"]
        assists = e.get("assistingParticipantIds", [])
        killer = e.get("killerId", 0)
        n_side = (1 if 1 <= killer <= 10 else 0) + len(assists)
        early = t < EARLY_MS
        if killer == pid:
            kills_pre += early
        if pid in assists:
            assists_pre += early
        if n_side >= FIGHT_SIZE and _involved(e, pid):
            fight_ta += 1
        if e.get("victimId") != pid:
            continue
        deaths_pre += early
        if n_side >= FIGHT_SIZE:
            fight_deaths += 1
        solo = 1 <= killer <= 10 and not assists
        solo_deaths += solo
        ahead = False
        if opp is not None:
            ga, gb = tl.gold_at(t, pid), tl.gold_at(t, opp)
            ahead = ga is not None and gb is not None and ga - gb >= AHEAD_GOLD
        ahead_deaths += ahead
        before_obj = any(t < u <= t + OBJECTIVE_WINDOW_MS
                         for u in enemy_obj_times)
        obj_deaths += before_obj
        pos = e.get("position") or {}
        x, y = pos.get("x", 0), pos.get("y", 0)
        kp = by_pid.get(killer)
        deaths.append(Death(
            t=round(t / 60_000, 2), x=x, y=y, zone=zone(x, y), solo=bool(solo),
            while_ahead=bool(ahead), before_enemy_objective=bool(before_obj),
            enemy_half=in_enemy_half(x, y, team),
            killer_champion=kp.get("championName") if kp else None))

    # deaths while ahead, per 10 minutes spent ahead (an opportunity-
    # adjusted rate: a raw count would punish players who are ahead more)
    if opp is not None:
        mins_ahead = 0
        for m in range(1, len(tl.frames)):
            a, b = tl.pf(m, pid), tl.pf(m, opp)
            if a and b and a["totalGold"] - b["totalGold"] >= AHEAD_GOLD:
                mins_ahead += 1
        if mins_ahead >= 3:
            s["deaths_ahead_rate"] = 10.0 * ahead_deaths / mins_ahead
    s.update(kills_pre14=kills_pre, deaths_pre14=deaths_pre,
             takedowns_pre14=kills_pre + assists_pre,
             early_death=float(deaths_pre > 0), solo_deaths=solo_deaths,
             deaths_before_enemy_obj=obj_deaths,
             fight_ka_per_death=fight_ta / max(fight_deaths, 1))

    team_epic = [e for e in tl.elite if e.get("killerTeamId") == team
                 and e.get("monsterType") in EPIC_MONSTERS]
    enemy_towers = [e for e in tl.buildings
                    if e.get("teamId") != team
                    and e.get("buildingType") == "TOWER_BUILDING"]
    epic_in = sum(_involved(e, pid) for e in team_epic)
    tower_in = sum(_involved(e, pid) for e in enemy_towers)
    s["epic_part"] = _div(epic_in, len(team_epic))
    s["obj_part"] = _div(epic_in + tower_in, len(team_epic) + len(enemy_towers))
    # Plates this player last-hit (timeline). Riot's
    # challenges.turretPlatesTaken counts takedowns incl. assists, so it
    # double-counts across a duo lane; last hits are unambiguous.
    s["turret_plates"] = sum(e.get("killerId") == pid for e in tl.plates)
    s["early_towers"] = sum(_involved(e, pid) for e in enemy_towers
                            if e["timestamp"] < EARLY_MS)

    if legendary:
        first = next((t for t, item in tl.purchases.get(pid, [])
                      if item in legendary), None)
        if first is not None:
            s["first_legendary_min"] = round(first / 60_000, 2)


def is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)
