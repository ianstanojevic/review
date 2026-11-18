"""Registry of per-game metrics: labels, formatting and polarity.

polarity: +1 higher is better, -1 lower is better, 0 neutral (no verdict).
early:    lane-phase / pre-14:00 stat. These are less contaminated by the
          result (a won game inflates whole-game stats like KP or gold/min),
          so they are better evidence of *cause* in wins-vs-losses.
timeline: needs the match timeline (NaN when Riot has none).
zero_sum: your value is minus your lane opponent's (lane diffs) or 1 minus
          the enemy team's (objective share). For these the peer pool
          EXCLUDES your direct opponents, or your own lead would be counted
          twice (you +500 vs a pool of opponents at −500).
family:   used to avoid near-duplicate findings (max 2 per family).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    fmt: str            # "pct" | "+.0f" | ".2f" …
    polarity: int
    family: str
    early: bool = False
    timeline: bool = False
    zero_sum: bool = False   # your gain is your direct opponent's loss


_M = [
    # fighting / damage
    Metric("kda", "KDA", ".2f", +1, "kda"),
    Metric("dmg_per_min", "Damage to champions/min", ".0f", +1, "damage"),
    Metric("dmg_share", "Team damage share", "pct", +1, "damage"),
    Metric("dmg_per_gold", "Damage per gold", ".2f", +1, "damage"),
    Metric("solo_kills", "Solo kills", ".2f", +1, "kda"),
    Metric("fight_ka_per_death", "Fight takedowns per fight death", ".2f",
           +1, "kda", timeline=True),
    # farming
    Metric("cs_per_min", "CS/min", ".2f", +1, "cs"),
    Metric("cs_share", "Team CS share", "pct", +1, "cs"),
    Metric("gold_per_min", "Gold/min", ".0f", +1, "gold"),
    Metric("first_legendary_min", "First legendary item (min)", ".1f", -1,
           "gold", timeline=True),
    # lane phase vs direct opponent
    Metric("cs_diff_10", "CS diff @10", "+.1f", +1, "lane", True, True, True),
    Metric("cs_diff_15", "CS diff @15", "+.1f", +1, "lane", True, True, True),
    Metric("gold_diff_10", "Gold diff @10", "+.0f", +1, "lane", True, True, True),
    Metric("gold_diff_15", "Gold diff @15", "+.0f", +1, "lane", True, True, True),
    Metric("xp_diff_10", "XP diff @10", "+.0f", +1, "lane", True, True, True),
    Metric("xp_diff_15", "XP diff @15", "+.0f", +1, "lane", True, True, True),
    # vision
    Metric("vision_per_min", "Vision score/min", ".2f", +1, "vision"),
    Metric("wards_placed_per_min", "Wards placed/min", ".2f", +1, "vision"),
    Metric("wards_killed_per_min", "Wards killed/min", ".2f", +1, "vision"),
    Metric("control_wards", "Control wards bought", ".1f", +1, "vision"),
    # aggression / early game
    Metric("takedowns_pre14", "Takedowns before 14:00", ".2f", +1, "early",
           True, True),
    Metric("kills_pre14", "Kills before 14:00", ".2f", +1, "early", True,
           True),
    Metric("early_dmg", "Damage to champions by 14:00", ".0f", +1, "early",
           True, True),
    Metric("turret_plates", "Turret plates (last hits)", ".2f", +1, "early", True),
    Metric("early_towers", "Tower takedowns before 14:00", ".2f", +1, "early",
           True, True),
    Metric("fb_involved", "First blood involvement", "pct", +1, "early", True),
    # survival
    Metric("deaths", "Deaths", ".1f", -1, "deaths"),
    Metric("deaths_per_min", "Deaths/min", ".3f", -1, "deaths"),
    Metric("deaths_pre14", "Deaths before 14:00", ".2f", -1, "deaths", True,
           True),
    Metric("early_death", "Games with a death before 14:00", "pct", -1,
           "deaths", True, True),
    Metric("solo_deaths", "Solo deaths", ".2f", -1, "deaths", timeline=True),
    Metric("deaths_ahead_rate", "Deaths per 10 min while ahead in lane",
           ".2f", -1, "deaths", timeline=True),
    Metric("deaths_before_enemy_obj",
           "Deaths followed by an enemy objective ≤60 s", ".2f", -1, "deaths",
           timeline=True),
    Metric("mitigated_per_death", "Damage mitigated per death", ".0f", +1,
           "tank"),
    Metric("dmg_taken_share", "Team damage taken share", "pct", 0, "tank"),
    # teamplay
    Metric("kp", "Kill participation", "pct", +1, "kp"),
    Metric("cc_per_min", "Time CCing enemies (s/min)", ".2f", +1, "teamplay"),
    Metric("ally_support_per_min", "Heals + shields on allies/min", ".0f", +1,
           "teamplay"),
    # objectives
    Metric("obj_part", "Objective participation", "pct", +1, "objectives",
           timeline=True),
    Metric("epic_part", "Epic monster participation", "pct", +1, "objectives",
           timeline=True),
    Metric("turret_dmg_per_min", "Turret damage/min", ".0f", +1,
           "objectives"),
    Metric("team_obj_control", "Team share of epic monsters", "pct", +1,
           "objectives", zero_sum=True),
]
METRICS: dict[str, Metric] = {m.key: m for m in _M}


def fmt_value(key: str, v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    m = METRICS.get(key)
    spec = m.fmt if m else ".2f"
    if spec == "pct":
        return "%.0f%%" % (100 * v)
    return format(v, spec)
