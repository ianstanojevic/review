"""Coarse Summoner's Rift zones from timeline (x, y) positions.

Map coordinates run ~0..14 870 (x) by ~0..14 980 (y), blue base bottom-left.
The river is the anti-diagonal x + y ≈ 14 800; mid lane is the diagonal
x ≈ y. Zones are deliberately coarse — they only need to be right enough to
say "most of your early deaths are in the bot-side river".
"""
from __future__ import annotations

RIVER = 14800.0
RIVER_HALF_WIDTH = 1300.0
MID_HALF_WIDTH = 1300.0
EDGE = 2300.0          # outer lanes hug the map edges
FAR = 14800.0 - EDGE

ZONE_LABEL = {
    "blue_base": "blue base", "red_base": "red base",
    "top_lane": "top lane", "mid_lane": "mid lane", "bot_lane": "bot lane",
    "river_top": "top-side river", "river_bot": "bot-side river",
    "blue_jungle_top": "blue jungle (top side)",
    "blue_jungle_bot": "blue jungle (bot side)",
    "red_jungle_top": "red jungle (top side)",
    "red_jungle_bot": "red jungle (bot side)",
}


def zone(x: float, y: float) -> str:
    s = x + y
    if s < 5600:
        return "blue_base"
    if s > 24000:
        return "red_base"
    if x < EDGE or y > FAR:
        return "top_lane"
    if y < EDGE or x > FAR:
        return "bot_lane"
    if abs(x - y) < MID_HALF_WIDTH:
        return "mid_lane"
    top_side = y > x
    if abs(s - RIVER) < RIVER_HALF_WIDTH:
        return "river_top" if top_side else "river_bot"
    side = "blue" if s < RIVER else "red"
    return "%s_jungle_%s" % (side, "top" if top_side else "bot")


def relative_zone(z: str, team: int) -> str:
    """Rename blue/red jungle & base to own/enemy for the given team."""
    own, enemy = ("blue", "red") if team == 100 else ("red", "blue")
    return z.replace(own + "_", "own_").replace(enemy + "_", "enemy_")


RELATIVE_LABEL = {
    "own_base": "your base", "enemy_base": "the enemy base",
    "top_lane": "top lane", "mid_lane": "mid lane", "bot_lane": "bot lane",
    "river_top": "top-side river", "river_bot": "bot-side river",
    "own_jungle_top": "your jungle (top side)",
    "own_jungle_bot": "your jungle (bot side)",
    "enemy_jungle_top": "the enemy jungle (top side)",
    "enemy_jungle_bot": "the enemy jungle (bot side)",
}


def in_enemy_half(x: float, y: float, team: int) -> bool:
    return (x + y > RIVER) if team == 100 else (x + y < RIVER)
