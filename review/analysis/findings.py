"""Rule-based strengths & weaknesses in plain English.

For each candidate stat: your mean over your games vs the role-matched peer
baseline (each of your games contributes its own role's peers).
* z = difference / SE (your game-to-game noise + the peer mean's noise)
* effect size d = difference / peer SD
* confidence: high |z| ≥ 2.58, medium |z| ≥ 1.96; anything weaker, any
  effect below d = 0.15, or fewer games than the stat's minimum is dropped.
Ranked by |d| × confidence; at most one finding per stat family.
Each finding carries the numbers, n, confidence and one concrete tip.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .aggregates import Game, col
from .metrics import METRICS, fmt_value
from .model import ROLE_LABEL
from .peers import PeerBaseline
from .stats import Rate, arr, wilson
from .zones import RELATIVE_LABEL, relative_zone

MIN_GAMES = 20
MIN_D = 0.15
Z_HIGH, Z_MED = 2.576, 1.96
PER_FAMILY = 1
MAX_EACH = 6
CHAMP_MIN_GAMES = 8

CANDIDATES = (
    "early_death", "deaths_pre14", "deaths_per_min", "solo_deaths",
    "deaths_ahead_rate", "deaths_before_enemy_obj",
    "cs_per_min", "cs_diff_10", "cs_diff_15", "gold_diff_10", "gold_diff_15",
    "xp_diff_15", "gold_per_min", "first_legendary_min",
    "dmg_per_min", "dmg_share", "dmg_per_gold", "kda", "solo_kills",
    "fight_ka_per_death",
    "kp", "obj_part", "epic_part", "turret_dmg_per_min", "turret_plates",
    "takedowns_pre14", "early_dmg",
    "vision_per_min", "wards_placed_per_min", "wards_killed_per_min",
    "control_wards", "cc_per_min", "ally_support_per_min",
)

# How each stat is said out loud. {you} {peer} {n} are filled in.
SENTENCE = {
    "early_death": "You die before 14:00 in {you} of games (peers: {peer}, n={n}).",
    "deaths_pre14": "You average {you} deaths before 14:00 (peers: {peer}, n={n}).",
    "deaths_per_min": "You die {you} times per minute (peers: {peer}, n={n}).",
    "solo_deaths": "You're solo-killed {you} times a game (peers: {peer}, n={n}).",
    "deaths_ahead_rate": "While ahead of your lane opponent you die {you} times per 10 min (peers: {peer}, n={n}).",
    "deaths_before_enemy_obj": "{you} of your deaths per game are followed by an enemy objective within 60 s (peers: {peer}, n={n}).",
    "cs_per_min": "Your CS/min is {you} (same-role peers: {peer}, n={n}).",
    "gold_diff_15": "You're {you} gold vs your lane opponent at 15:00 (peers: {peer}, n={n}).",
    "gold_diff_10": "You're {you} gold vs your lane opponent at 10:00 (peers: {peer}, n={n}).",
    "cs_diff_10": "You're {you} CS vs your lane opponent at 10:00 (peers: {peer}, n={n}).",
    "cs_diff_15": "You're {you} CS vs your lane opponent at 15:00 (peers: {peer}, n={n}).",
    "xp_diff_15": "You're {you} XP vs your lane opponent at 15:00 (peers: {peer}, n={n}).",
    "first_legendary_min": "You finish your first legendary at {you} min (peers: {peer}, n={n}).",
    "kp": "Your kill participation is {you} (peers: {peer}, n={n}).",
}

TIPS_WEAK = {
    "cs_per_min": "Drill last-hitting in practice tool (target +0.5 CS/min), and catch the side wave before every objective instead of walking up empty-handed.",
    "cs_diff_10": "Your first 10 minutes bleed CS: track the wave state — freeze when you're weaker, and don't trade while a cannon is about to die.",
    "cs_diff_15": "You fall behind in CS by 15:00: shove before each back so you don't lose a wave to the recall, and time recalls with the cannon wave.",
    "gold_diff_10": "You're behind in gold at 10: play the first three waves for CS and plates, not for kills, until you've won a few lanes this way.",
    "gold_diff_15": "Lost gold by 15:00 compounds: after a won trade, convert it to a plate or a crash-and-back instead of staying to chase.",
    "xp_diff_15": "XP deficit at 15: you miss waves while roaming/backing — never let a full wave crash into your tower without you.",
    "gold_per_min": "Low gold/min: when there's nothing to fight for, farm the side lane closest to the next objective.",
    "first_legendary_min": "Your first item comes late: plan backs around gold thresholds (e.g. ≥1300 for a component) and don't back with 600.",
    "dmg_per_min": "Low damage output: stand at max range in fights and use every cooldown on whoever is closest — dead time in a fight is lost damage.",
    "dmg_share": "Your share of team damage is low: position to hit the frontline continuously instead of waiting for the perfect target.",
    "dmg_per_gold": "Your gold isn't converting into damage: review item order and fight timing — a full item spike is the moment to force.",
    "kda": "Your KDA trails peers: before each fight, check where the enemy jungler and the closest threat are — most deaths come from one missing enemy.",
    "solo_kills": "Few solo kills: look for trades when your opponent uses their main ability on the wave.",
    "fight_ka_per_death": "You die a lot in teamfights relative to your takedowns: hold your position behind the frontline until the engage has been used.",
    "kp": "Low kill participation: when the wave is pushed, move toward the next fight/objective instead of farming away from your team.",
    "obj_part": "You're missing objectives: start moving 45 s before dragon/Herald/Grubs spawn with the wave pushed.",
    "epic_part": "You're absent for epic monsters: set a habit — when the objective timer hits 1:00, shove and walk.",
    "turret_dmg_per_min": "Low turret damage: after a won fight, hit the tower before resetting.",
    "turret_plates": "Few plates: when your opponent backs or dies before 14:00, hit the tower first and only then the wave.",
    "takedowns_pre14": "Low early impact: track the enemy jungler and be ready to collapse when your jungler ganks or invades.",
    "early_dmg": "Low early damage: trade when the enemy laner's main ability is on cooldown — and use your level-2 and level-6 spikes.",
    "vision_per_min": "Low vision score: buy a control ward on every back and use your trinket on cooldown.",
    "wards_placed_per_min": "Few wards placed: use your trinket on cooldown, especially in the 60 s before objectives.",
    "wards_killed_per_min": "Few wards cleared: buy a control ward and sweep the objective pit area before each dragon.",
    "control_wards": "Few control wards: buy one every back — it's the cheapest gold-to-win-rate item in the game.",
    "cc_per_min": "Low CC on enemies: hold your CC for the enemy's engage tool instead of using it on the wave.",
    "ally_support_per_min": "Few heals/shields: hold shields for the moment your carry is targeted, not on cooldown.",
    "deaths_per_min": "You die more often than peers: before each fight, ask \"where is the enemy jungler?\" — most deaths come from a missing enemy.",
    "solo_deaths": "You lose too many 1v1s: respect the enemy's level-6 and item spikes, and don't stay in lane at low HP for one more wave.",
    "deaths_ahead_rate": "You throw leads: when you're ahead, back on a pushed wave and spend the gold — overstaying while ahead is how you give it back.",
    "deaths_before_enemy_obj": "Your deaths hand out objectives: in the 90 s before dragon/Baron, don't take side-lane fights — group or hold vision.",
}

TIPS_STRONG = {
    "lane": "Your lane phase is a real edge — convert it: after a won lane, rotate to the next objective instead of staying for more farm.",
    "cs": "Your farming is above your peers — keep it, and turn it into tempo by grouping right when your item completes.",
    "gold": "You get gold faster than peers — use the spikes: force a fight or objective right after each item.",
    "damage": "You out-damage your peers — protect that by playing for long fights where your damage adds up.",
    "kda": "You win fights efficiently — look for more of them around objectives, where kills are worth the most.",
    "kp": "You show up for your team — keep it up; arriving early lets you pick the fight's angle.",
    "objectives": "You're present for objectives — make it a habit to start each objective setup with a control ward.",
    "early": "Your early game hits hard — snowball it with plates and the first tower.",
    "vision": "Your vision control is above your peers — use it: fight where your wards are.",
    "teamplay": "You enable your team — keep doing it.",
    "deaths": "You die less than your peers — that safety is a win condition; don't lose it chasing kills.",
    "tank": "You soak damage well — keep fronting fights for your team.",
}


TIPS_STRONG_KEY = {
    "turret_dmg_per_min": "You pressure towers well — after every won fight, take the closest tower before resetting.",
    "turret_plates": "You take plates well — keep trading plates for pressure when your opponent backs.",
    "control_wards": "You invest in control wards more than peers — place them where the next objective fight will be.",
    "deaths_per_min": "You die less often than peers — that safety is a win condition; don't trade it for risky chases.",
    "deaths_pre14": "Your early game is safe — use that stability to take plates and first tower.",
}


@dataclass
class Finding:
    kind: str           # "strength" | "weakness"
    key: str            # metric key or "champion:<name>"
    title: str
    text: str           # headline sentence with numbers
    detail: str         # extra context derived from the data
    tip: str
    value: float
    peer: float
    n: int
    confidence: str     # "high" | "medium"
    effect: float       # |d| (or WR gap for champion findings)
    z: float


def _early_death_detail(games: list[Game]) -> tuple[str, str]:
    zones, minutes, total = Counter(), [], 0
    for _, p in games:
        for d in p.deaths:
            if d.t < 14:
                zones[relative_zone(d.zone, p.team)] += 1
                minutes.append(d.t)
                total += 1
    if not total:
        return "", TIPS_WEAK["deaths_per_min"]
    z, c = zones.most_common(1)[0]
    where = RELATIVE_LABEL[z]
    med = float(np.median(minutes))
    detail = ("Most early deaths are in %s (%d of %d); median time %d:%02d."
              % (where, c, total, int(med), int(med % 1 * 60)))
    tip = {
        "river_bot": "ward the bot-side river/pixel brush before 3:00 and track where the enemy jungler started",
        "river_top": "ward the top-side river before you walk through it, and only contest scuttle with lane priority",
        "bot_lane": "respect the enemy's level-2/level-6 spikes and the jungler's first gank window (~3:00) — keep a ward in tri-brush",
        "top_lane": "keep a ward in the river/tri-brush and don't push past the middle of lane without knowing where the jungler is",
        "mid_lane": "ward both side brushes and back off when both enemy side-laners are missing",
        "enemy_jungle_bot": "stop facechecking the enemy jungle — only go in with lane priority and a ward ahead of you",
        "enemy_jungle_top": "stop facechecking the enemy jungle — only go in with lane priority and a ward ahead of you",
        "own_jungle_bot": "you get caught in your own jungle — ward your entrance and don't walk alone after shoving",
        "own_jungle_top": "you get caught in your own jungle — ward your entrance and don't walk alone after shoving",
    }.get(z, "check where the enemy jungler is before every trade")
    return detail, "Most early deaths: %s → %s." % (where, tip)


def _pooled_kda(pgs) -> float:
    ka = sum(p.stats["kills"] + p.stats["assists"] for p in pgs)
    return ka / max(sum(p.stats["deaths"] for p in pgs), 1)


def _rename_peers(s: str, word: str | None) -> str:
    """'peers' -> e.g. 'Master+ players' when scoring against a benchmark."""
    if not word or not s:
        return s
    return (s.replace("same-role peers", word).replace("your peers", word)
            .replace("peers", word))


def stat_findings(games: list[Game], baseline: PeerBaseline,
                  peer_word: str | None = None) -> list[Finding]:
    roles = [p.role for _, p in games]
    out = []
    for key in CANDIDATES:
        m = METRICS[key]
        x = arr(col(games, key))
        n = len(x)
        if n < MIN_GAMES or m.polarity == 0:
            continue
        # role-matched baseline over the games that have this stat
        rs = [p.role for _, p in games
              if not math.isnan(p.stats.get(key, np.nan))]
        bmean, bmed, bvar, bvom, n_peers = baseline.role_matched(key, rs)
        if math.isnan(bmean) or bvar <= 0:
            continue
        diff = float(x.mean()) - bmean
        se = math.sqrt(x.var(ddof=1) / n + bvom)
        z = diff / se if se > 0 else 0.0
        d = diff / math.sqrt(bvar)
        conf = "high" if abs(z) >= Z_HIGH else "medium" if abs(z) >= Z_MED else None
        if conf is None or abs(d) < MIN_D:
            continue
        good = diff * m.polarity > 0
        you, peer = fmt_value(key, float(x.mean())), fmt_value(key, bmean)
        if key == "kda":
            # the test uses per-game KDA, but say the pooled (K+A)/D that
            # players know from the scoreboard
            you = "%.2f" % _pooled_kda([p for _, p in games])
            peer = "%.2f" % _pooled_kda([q for r in set(rs) if r
                                         for q in baseline.games[r]])
        text = SENTENCE.get(key, "{label}: {you} vs {peer} for same-role peers (n={n}).")
        text = text.format(label=m.label, you=you, peer=peer, n=n)
        detail, tip = "", ""
        if good:
            tip = TIPS_STRONG_KEY.get(key) or TIPS_STRONG.get(m.family,
                                                              "Keep it up.")
        elif key in ("early_death", "deaths_pre14"):
            detail, tip = _early_death_detail(games)
        else:
            tip = TIPS_WEAK.get(key, "")
        text, tip = _rename_peers(text, peer_word), _rename_peers(tip,
                                                                  peer_word)
        out.append(Finding(
            "strength" if good else "weakness", key, m.label, text, detail,
            tip, float(x.mean()), bmean, n, conf, abs(d), z))
    return out


def champion_findings(games: list[Game]) -> list[Finding]:
    by: dict[str, list] = {}
    for _, p in games:
        by.setdefault(p.champion, []).append(p)
    total = Rate(sum(p.win for _, p in games), len(games))
    out = []
    for champ, ps in by.items():
        n = len(ps)
        if n < CHAMP_MIN_GAMES:
            continue
        w = sum(p.win for p in ps)
        lo, hi = wilson(w, n)
        others = [p for _, p in games if p.champion != champ]
        gd = arr([p.stats.get("gold_diff_15", np.nan) for p in ps])
        gd_o = arr([p.stats.get("gold_diff_15", np.nan) for p in others])
        detail = ""
        if len(gd) >= 5 and len(gd_o) >= 5:
            detail = ("GD@15 on %s: %+.0f vs %+.0f on your other champions."
                      % (champ, gd.mean(), gd_o.mean()))
        if hi < 0.5:
            out.append(Finding(
                "weakness", "champion:" + champ, champ,
                "%s: %dW %dL (%.0f%%, 90%% CI %.0f–%.0f%%, n=%d)."
                % (champ, w, n - w, 100 * w / n, 100 * lo, 100 * hi, n),
                detail,
                "Bench %s in ranked for now, or review three of its losses — "
                "a win rate this low over %d games is unlikely to be bad luck."
                % (champ, n), w / n, total.p, n,
                "high" if hi < 0.45 else "medium", 0.5 - w / n, 0.0))
        elif lo > 0.5:
            out.append(Finding(
                "strength", "champion:" + champ, champ,
                "%s: %dW %dL (%.0f%%, 90%% CI %.0f–%.0f%%, n=%d)."
                % (champ, w, n - w, 100 * w / n, 100 * lo, 100 * hi, n),
                detail, "%s is a proven pick for you — prioritise it (and "
                "ban its counters) in champ select." % champ, w / n, total.p,
                n, "high" if lo > 0.55 else "medium", w / n - 0.5, 0.0))
    return out


def rank_findings(found: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    weight = {"high": 1.0, "medium": 0.7}
    found = sorted(found, key=lambda f: -f.effect * weight[f.confidence])
    strengths, weaknesses = [], []
    per_family: Counter = Counter()
    for f in found:
        fam = METRICS[f.key].family if f.key in METRICS else f.key
        bucket = strengths if f.kind == "strength" else weaknesses
        if per_family[(f.kind, fam)] >= PER_FAMILY or len(bucket) >= MAX_EACH:
            continue
        per_family[(f.kind, fam)] += 1
        bucket.append(f)
    return strengths, weaknesses


def role_note(roles: list[str | None]) -> str:
    c = Counter(r for r in roles if r)
    return ", ".join("%s %d" % (ROLE_LABEL[r], n) for r, n in c.most_common())
