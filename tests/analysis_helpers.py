"""Shared helpers for analysis tests: fixtures and a synthetic game builder."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import numpy as np

from review.analysis.metrics import METRICS
from review.analysis.model import ROLES, MatchFeatures, PlayerGame

FIX = Path(__file__).parent / "fixtures"
ME = "puuid-me"


def load_fixture(name: str) -> tuple[dict, dict | None]:
    with gzip.open(FIX / ("%s.json.gz" % name), "rt", encoding="utf-8") as f:
        d = json.load(f)
    return d["match"], d["timeline"]


def variant(match: dict, match_id: str, **info_changes) -> dict:
    m = copy.deepcopy(match)
    m["metadata"]["matchId"] = match_id
    m["info"].update(info_changes)
    return m


def synth_games(n: int, boost: float = 0.0, role: str = "BOTTOM",
                seed: int = 0, start_ms: int = 1_700_000_000_000,
                trend: dict | None = None, win_p: float | None = None
                ) -> list[MatchFeatures]:
    """n games where every stat ~ N(10, 2) for everyone, except the player
    ("puuid-me", team 100, ``role``) is shifted by ``boost`` SDs in the good
    direction. ``trend`` = {metric: total drift over the n games}."""
    rng = np.random.default_rng(seed)
    out = []
    for g in range(n):
        players = []
        for pid in range(1, 11):
            team = 100 if pid <= 5 else 200
            r = ROLES[(pid - 1) % 5]
            stats = {k: float(rng.normal(10, 2)) for k in METRICS}
            stats.update(kills=float(rng.integers(0, 10)),
                         deaths=float(rng.integers(0, 10)),
                         assists=float(rng.integers(0, 10)),
                         cs=float(rng.integers(100, 250)),
                         early_death=float(rng.random() < 0.5),
                         fb_involved=float(rng.random() < 0.2))
            players.append(PlayerGame(
                puuid="p%d-%d" % (g, pid), pid=pid, team=team, role=r,
                champion="Champ%d" % (pid % 3), champion_id=pid,
                win=team == 100, riot_id="P#%d" % pid, opponent_pid=None,
                opponent_champion=None, stats=stats))
        me = next(p for p in players if p.team == 100 and p.role == role)
        opp = next(p for p in players if p.team == 200 and p.role == role)
        me.puuid, me.champion = ME, "Mine"
        me.opponent_pid, opp.opponent_pid = opp.pid, me.pid
        me.opponent_champion = opp.champion
        for k, m in METRICS.items():
            if m.polarity and not m.zero_sum:
                me.stats[k] += boost * 2 * m.polarity
        for k, drift in (trend or {}).items():
            me.stats[k] += drift * g / max(n - 1, 1)
        for k, m in METRICS.items():   # zero-sum pairs stay mirror images
            if m.zero_sum:
                me.stats[k] = float(rng.normal(boost * 2, 2))
                opp.stats[k] = (1 - me.stats[k]) if k == "team_obj_control" \
                    else -me.stats[k]
        win = (rng.random() < (win_p if win_p is not None else 0.5))
        for p in players:
            p.win = (p.team == 100) == win
            p.stats["win"] = float(p.win)
        t = start_ms + g * 3_600_000 * 3
        out.append(MatchFeatures(
            match_id="SYN_%d" % g, queue_id=420, map_id=11, creation_ms=t,
            end_ms=t + 1_800_000, duration_s=1800, patch="16.%d" % (20 + g * 3 // n),
            remake=False, has_timeline=True, players=players))
    return out
