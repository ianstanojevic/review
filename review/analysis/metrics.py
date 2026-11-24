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


# One plain sentence per stat, for hover tooltips ("what is this?").
HELP: dict[str, str] = {
    "kda": "(Kills + assists) ÷ deaths, per game. Higher is better.",
    "dmg_per_min": "Damage you dealt to enemy champions per minute.",
    "dmg_share": "Your part of your team's damage to champions.",
    "dmg_per_gold": "Damage to champions for every gold you earned — how "
                    "well you turn gold into damage.",
    "solo_kills": "Kills you got with no help from a teammate.",
    "fight_ka_per_death": "In fights: your kills + assists for every time "
                          "you died.",
    "cs_per_min": "Minions and monsters you killed per minute.",
    "cs_share": "Your part of your team's minion and monster kills.",
    "gold_per_min": "Gold you earned per minute.",
    "first_legendary_min": "The minute you finished your first legendary "
                           "item. Lower is better.",
    "cs_diff_10": "Your CS minus your lane opponent's at 10:00.",
    "cs_diff_15": "Your CS minus your lane opponent's at 15:00.",
    "gold_diff_10": "Your gold minus your lane opponent's at 10:00.",
    "gold_diff_15": "Your gold minus your lane opponent's at 15:00.",
    "xp_diff_10": "Your XP minus your lane opponent's at 10:00.",
    "xp_diff_15": "Your XP minus your lane opponent's at 15:00.",
    "vision_per_min": "Riot's vision score per minute: wards placed, wards "
                      "cleared and time they gave sight.",
    "wards_placed_per_min": "Wards you placed per minute.",
    "wards_killed_per_min": "Enemy wards you destroyed per minute.",
    "control_wards": "Control wards you bought per game.",
    "takedowns_pre14": "Kills + assists before 14:00.",
    "kills_pre14": "Kills before 14:00.",
    "early_dmg": "Damage to champions in the first 14 minutes.",
    "turret_plates": "Turret plates you last-hit (before 14:00).",
    "early_towers": "Towers you helped take before 14:00.",
    "fb_involved": "Share of games where you got or assisted first blood.",
    "deaths": "Deaths per game.",
    "deaths_per_min": "Deaths per minute of game time. Lower is better.",
    "deaths_pre14": "Deaths before 14:00. Lower is better.",
    "early_death": "Share of games where you died before 14:00.",
    "solo_deaths": "Deaths where only one enemy took part — often caught "
                   "alone.",
    "deaths_ahead_rate": "How often you die while ahead of your lane "
                         "opponent — throwing a lead.",
    "deaths_before_enemy_obj": "Deaths the enemy turned into a dragon, "
                               "Herald, Baron or tower within 60 seconds.",
    "mitigated_per_death": "Damage you blocked or absorbed for every death "
                           "— how much you soak before going down.",
    "dmg_taken_share": "Your part of the damage your team took.",
    "kp": "Share of your team's kills you got or assisted.",
    "cc_per_min": "Seconds of crowd control (stuns, slows…) you put on "
                  "enemies per minute.",
    "ally_support_per_min": "Healing and shielding you gave teammates per "
                            "minute.",
    "obj_part": "Share of your team's objectives (towers, dragons, Barons) "
                "you helped take.",
    "epic_part": "Share of your team's dragons, Heralds and Barons you were "
                 "there for.",
    "turret_dmg_per_min": "Damage you dealt to towers per minute.",
    "team_obj_control": "Share of all epic monsters in the game your team "
                        "took.",
}


def help_text(key: str) -> str | None:
    return HELP.get(key)
