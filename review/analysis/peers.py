"""Peer baseline: the other players in the same role in YOUR games.

For every role, the pool is every non-you participant who played that role
in any of your (non-remake, ranked) games — both teams. They were matched
to your MMR by Riot's matchmaker, so this is a fair, Elo-matched benchmark
without inventing external numbers. A Bot main with 150 games gets ~170
ADC peer games; even off-roles get ~2 peers per game.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

import numpy as np

from .metrics import METRICS
from .model import MatchFeatures, PlayerGame
from .stats import arr, percentile_in


class PeerBaseline:
    def __init__(self, matches: Iterable[MatchFeatures], puuid: str):
        self.games: dict[str, list[PlayerGame]] = defaultdict(list)
        # same pool minus your direct lane opponents — used for zero-sum
        # metrics, where the opponent's value is minus yours by definition
        self.non_opponents: dict[str, list[PlayerGame]] = defaultdict(list)
        for m in matches:
            me = m.player(puuid)
            opp = me.opponent_pid if me else None
            for p in m.players:
                if p.puuid != puuid and p.role:
                    self.games[p.role].append(p)
                    if p.pid != opp:
                        self.non_opponents[p.role].append(p)
        self._cache: dict[tuple[str, str], np.ndarray] = {}

    def n(self, role: str) -> int:
        return len(self.games.get(role, ()))

    def values(self, role: str, key: str) -> np.ndarray:
        ck = (role, key)
        if ck not in self._cache:
            m = METRICS.get(key)
            pool = (self.non_opponents if m and m.zero_sum else self.games)
            v = arr([p.stats.get(key, np.nan) for p in pool.get(role, ())])
            if m and m.zero_sum:
                # every pairing has a mirror image: (+x, −x) for diffs,
                # (s, 1 − s) for team shares -> exactly symmetric baseline
                v = np.concatenate([v, 1.0 - v if key == "team_obj_control"
                                    else -v])
            self._cache[ck] = np.sort(v)
        return self._cache[ck]

    @staticmethod
    def exact_mean(key: str) -> bool:
        """Zero-sum baselines have a known population mean (0 or 50 %)."""
        m = METRICS.get(key)
        return bool(m and m.zero_sum)

    def percentile(self, role: str, key: str, xs) -> np.ndarray:
        return percentile_in(self.values(role, key), np.asarray(xs, float))

    def stat(self, role: str, key: str, fn) -> float:
        v = self.values(role, key)
        return float(fn(v)) if len(v) else float("nan")

    def role_matched(self, key: str, roles: Iterable[str | None]):
        """Baseline for a set of your games: each game contributes its own
        role's peer stats. -> (mean, median, var, var_of_mean, n_peers)."""
        counts = Counter(r for r in roles if r)
        total = sum(counts.values())
        if not total:
            return (float("nan"),) * 4 + (0,)
        mean = median = var = vom = 0.0
        n_peers = 0
        for role, c in counts.items():
            v = self.values(role, key)
            if len(v) < 2:
                return (float("nan"),) * 4 + (0,)
            w = c / total
            mean += w * v.mean()
            median += w * float(np.median(v))
            var += w * v.var(ddof=1)
            if not self.exact_mean(key):
                vom += w * w * v.var(ddof=1) / len(v)
            n_peers += len(v)
        return mean, median, var, vom, n_peers
