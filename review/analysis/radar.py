"""Performance radar: eight 0–100 axes against same-role peers.

Method (no shortcuts):
1. Every sub-metric of every game -> mid-rank percentile within that ROLE's
   peer pool (supports vs supports …); "lower is better" metrics inverted.
2. Game axis score = weighted mean of the available sub-metric percentiles
   with that game's ROLE weights (weights.toml, role overrides).
3. Axis score = mean over games, shrunk toward 50 by τ²/(τ² + σ²/n), so six
   games can't show a 98. σ² blends your game-to-game variance with the
   peers' (10 pseudo-games), so a few lucky blow-outs can't fake certainty. An axis score therefore reads as "how often you
   beat a typical same-role peer at your MMR".
4. 90 % CI: bootstrap over games (1000 resamples, fixed seed, so the same
   input always gives the same output and results can be cached).

Consistency is within-player, so it is built differently:
* steadiness — each game's |game score − your median| as a percentile among
  peers' |game score − peer median| (inverted). Matchmaking keeps peers'
  skill close to yours, so their spread approximates a typical player's
  game-to-game swing at your MMR. Small upward bias; documented.
* tilt — mean game score after a same-session loss minus after a win.
"""
from __future__ import annotations

import hashlib
import json
import math
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .metrics import METRICS, fmt_value
from .model import ROLES, PlayerGame
from .peers import PeerBaseline
from .stats import arr, nanmean_rows, percentile_in, shrink_weight

AXES = ("fighting", "farming", "vision", "aggression", "survival",
        "teamplay", "objectives", "consistency")
SCORED = AXES[:-1]
AXIS_LABEL = {a: a.capitalize() for a in AXES}
DEFAULT_CONFIG = Path(__file__).with_name("weights.toml")
UNIFORM_VAR = 100.0 ** 2 / 12     # variance of a percentile under "no effect"
PRIOR_GAMES = 10                  # pseudo-games of population noise


def pooled_var(v: np.ndarray, prior_var: float) -> float:
    """Per-game variance for shrinkage: your own variance blended with the
    population's, worth PRIOR_GAMES pseudo-games. Six blow-out games have a
    tiny sample variance; trusting it would barely shrink them."""
    n = len(v)
    own = float(v.var(ddof=1)) if n > 1 else prior_var
    return (PRIOR_GAMES * prior_var + (n - 1) * own) / (PRIOR_GAMES + n - 1)


@dataclass
class RadarConfig:
    axes: dict[str, dict[str, float]]
    roles: dict[str, dict[str, dict[str, float]]]
    overall: dict[str, float]
    prior_sd: float = 10.0
    iterations: int = 1000
    ci: float = 0.90
    seed: int = 1337
    min_games: int = 5
    tilt_weight: float = 0.35
    tilt_scale: float = 2.0
    session_gap_min: float = 60.0

    def weights(self, role: str | None, axis: str) -> dict[str, float]:
        w = dict(self.axes.get(axis, {}))
        if role:
            w.update(self.roles.get(role, {}).get(axis, {}))
        return w

    @property
    def metrics(self) -> list[str]:
        keys = {k for ax in self.axes.values() for k in ax}
        for r in self.roles.values():
            for ax in r.values():
                keys.update(ax)
        unknown = keys - set(METRICS)
        if unknown:
            raise ValueError("weights.toml: unknown metrics %s" % sorted(unknown))
        return sorted(keys)

    def fingerprint(self) -> str:
        blob = json.dumps(self.__dict__, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def load_config(path: str | Path | None = None) -> RadarConfig:
    with open(path or DEFAULT_CONFIG, "rb") as f:
        raw = tomllib.load(f)
    s = raw.get("settings", {})
    return RadarConfig(
        axes=raw.get("axes", {}), roles=raw.get("roles", {}),
        overall=raw.get("overall", {a: 1.0 for a in AXES}),
        prior_sd=s.get("prior_sd", 10.0),
        iterations=s.get("bootstrap_iterations", 1000), ci=s.get("ci", 0.9),
        seed=s.get("seed", 1337), min_games=s.get("min_games", 5),
        tilt_weight=s.get("consistency_tilt_weight", 0.35),
        tilt_scale=s.get("tilt_scale", 2.0),
        session_gap_min=s.get("session_gap_min", 60))


@dataclass
class SubScore:
    key: str
    label: str
    score: float          # shrunk mean percentile (0–100)
    n: int
    value: float          # your mean raw value
    peer_median: float    # role-matched peer median
    weight: float

    @property
    def value_text(self) -> str:
        return fmt_value(self.key, self.value)

    @property
    def peer_text(self) -> str:
        return fmt_value(self.key, self.peer_median)


@dataclass
class AxisScore:
    axis: str
    label: str
    score: float
    lo: float
    hi: float
    raw: float            # unshrunk mean
    n: int
    subs: list[SubScore] = field(default_factory=list)


@dataclass
class Radar:
    axes: list[AxisScore]
    overall: float
    overall_lo: float
    overall_hi: float
    n_games: int
    insufficient: bool = False

    def axis(self, name: str) -> AxisScore:
        return next(a for a in self.axes if a.axis == name)


class RadarModel:
    def __init__(self, baseline: PeerBaseline, cfg: RadarConfig):
        self.b, self.cfg = baseline, cfg
        self.keys = cfg.metrics
        self.kidx = {k: i for i, k in enumerate(self.keys)}
        self.sign = np.array([METRICS[k].polarity for k in self.keys])
        self.W = {r: self._weight_matrix(r) for r in ROLES}
        ow = np.array([cfg.overall.get(a, 1.0) for a in SCORED], float)
        self.overall_w_scored = ow
        self.overall_w = np.array([cfg.overall.get(a, 1.0) for a in AXES], float)
        # peer game-score spread per role, for Consistency, and the peers'
        # per-game axis-score variance: the population noise level that
        # anchors shrinkage (a variance estimated from 6 of your games is
        # itself too noisy to trust)
        self.peer_dev: dict[str, np.ndarray] = {}
        self.peer_sd: dict[str, float] = {}
        self.peer_axis_var: dict[str, np.ndarray] = {}
        for role in ROLES:
            pgs = baseline.games.get(role, [])
            if len(pgs) < 10:
                continue
            _, A, g = self.score_games(pgs)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                self.peer_axis_var[role] = np.nanvar(A, axis=0, ddof=1)
            g = arr(g)
            self.peer_dev[role] = np.sort(np.abs(g - np.median(g)))
            self.peer_sd[role] = float(g.std(ddof=1))

    def _prior_var(self, roles, j: int) -> float:
        vs = [self.peer_axis_var[r][j] for r in roles
              if r in self.peer_axis_var and np.isfinite(self.peer_axis_var[r][j])]
        return float(np.mean(vs)) if vs else UNIFORM_VAR

    def _weight_matrix(self, role: str) -> np.ndarray:
        W = np.zeros((len(SCORED), len(self.keys)))
        for j, ax in enumerate(SCORED):
            for k, w in self.cfg.weights(role, ax).items():
                W[j, self.kidx[k]] = w
        return W

    # -- per game --------------------------------------------------------
    def percentiles(self, pgs: list[PlayerGame]) -> np.ndarray:
        P = np.full((len(pgs), len(self.keys)), np.nan)
        by_role: dict[str, list[int]] = {}
        for i, p in enumerate(pgs):
            if p.role:
                by_role.setdefault(p.role, []).append(i)
        for role, rows in by_role.items():
            for k, j in self.kidx.items():
                xs = [pgs[i].stats.get(k, np.nan) for i in rows]
                pc = self.b.percentile(role, k, xs)
                P[rows, j] = 100.0 - pc if self.sign[j] < 0 else pc
        return P

    def axis_matrix(self, pgs: list[PlayerGame], P: np.ndarray) -> np.ndarray:
        A = np.full((len(pgs), len(SCORED)), np.nan)
        for i, p in enumerate(pgs):
            if not p.role:
                continue
            W = self.W[p.role]
            mask = ~np.isnan(P[i])
            num = W @ np.where(mask, P[i], 0.0)
            den = W @ mask.astype(float)
            with np.errstate(invalid="ignore", divide="ignore"):
                A[i] = np.where(den > 0, num / den, np.nan)
        return A

    def game_score(self, A: np.ndarray) -> np.ndarray:
        w = np.where(np.isnan(A), 0.0, self.overall_w_scored)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(w.sum(1) > 0,
                            (np.nan_to_num(A) * w).sum(1) / w.sum(1), np.nan)

    def score_games(self, pgs):
        P = self.percentiles(pgs)
        A = self.axis_matrix(pgs, P)
        return P, A, self.game_score(A)

    # -- radar -----------------------------------------------------------
    def radar(self, pgs: list[PlayerGame], prev_win: list[bool | None],
              compute_ci: bool = True, scored=None) -> Radar:
        cfg, tau = self.cfg, self.cfg.prior_sd
        n = len(pgs)
        P, A, g = scored if scored is not None else self.score_games(pgs)
        roles = [p.role for p in pgs]
        rng = np.random.default_rng(cfg.seed)
        B = cfg.iterations if compute_ci and n >= 2 else 0
        idx = rng.integers(0, n, (B, n)) if B else None
        qlo, qhi = (1 - cfg.ci) / 2, 1 - (1 - cfg.ci) / 2

        def shrunk(col, prior_var=UNIFORM_VAR):
            v = arr(col)
            if not len(v):
                return np.nan, np.nan, 0, 0.0
            w = shrink_weight(pooled_var(v, prior_var), len(v), tau)
            return 50 + w * (float(v.mean()) - 50), float(v.mean()), len(v), w

        def boot(col, w):
            if not B:
                return None
            return 50 + w * (nanmean_rows(col[idx]) - 50)

        axes, boots = [], []
        for j, ax in enumerate(SCORED):
            score, raw, na, w = shrunk(A[:, j], self._prior_var(roles, j))
            bs = boot(A[:, j], w) if na else None
            boots.append(bs)
            axes.append(self._axis(ax, score, raw, na, bs, qlo, qhi,
                                   self._subs(ax, pgs, P, roles)))

        # consistency
        cons, cons_boot = self._consistency(pgs, g, roles, prev_win, rng, B,
                                            idx, shrunk, boot)
        axes.append(cons)
        boots.append(cons_boot)

        scores = np.array([a.score for a in axes])
        ok = ~np.isnan(scores)
        ow = self.overall_w * ok
        overall = float((np.nan_to_num(scores) * ow).sum() / ow.sum()) \
            if ow.sum() else np.nan
        lo = hi = np.nan
        if B:
            mat = np.vstack([b if (b is not None and ok[i]) else np.zeros(B)
                             for i, b in enumerate(boots)])
            ob = (mat * ow[:, None]).sum(0) / ow.sum()
            lo, hi = np.nanquantile(ob, [qlo, qhi])
        return Radar(axes, overall, float(lo), float(hi), n,
                     insufficient=n < cfg.min_games)

    def _axis(self, ax, score, raw, n, bs, qlo, qhi, subs) -> AxisScore:
        lo = hi = np.nan
        if bs is not None and np.isfinite(bs).any():
            lo, hi = np.nanquantile(bs, [qlo, qhi])
        return AxisScore(ax, AXIS_LABEL[ax], float(score), float(lo),
                         float(hi), float(raw), n, subs)

    def _subs(self, ax, pgs, P, roles) -> list[SubScore]:
        weights: dict[str, list[float]] = {}
        for r in roles:
            if r:
                for k, w in self.cfg.weights(r, ax).items():
                    weights.setdefault(k, []).append(w)
        out = []
        tau = self.cfg.prior_sd
        for k, ws in weights.items():
            if not any(ws):
                continue
            col = arr(P[:, self.kidx[k]])
            if len(col):
                w = shrink_weight(pooled_var(col, UNIFORM_VAR), len(col), tau)
                score = 50 + w * (float(col.mean()) - 50)
            else:
                score = np.nan
            vals = arr([p.stats.get(k, np.nan) for p in pgs])
            out.append(SubScore(
                k, METRICS[k].label, float(score), len(col),
                float(vals.mean()) if len(vals) else np.nan,
                self.b.role_matched(k, roles)[1], float(np.mean(ws))))
        out.sort(key=lambda s: -s.weight)
        return out

    def _consistency(self, pgs, g, roles, prev_win, rng, B, idx, shrunk, boot):
        cfg, tau = self.cfg, self.cfg.prior_sd
        n = len(pgs)
        dev_pct = np.full(n, np.nan)
        gm = arr(g)
        if len(gm):
            med = float(np.median(gm))
            for i, r in enumerate(roles):
                if r in self.peer_dev and not np.isnan(g[i]):
                    dev_pct[i] = 100 - percentile_in(
                        self.peer_dev[r], np.array([abs(g[i] - med)]))[0]
        s_dev, raw_dev, n_dev, w_dev = shrunk(dev_pct)
        b_dev = boot(dev_pct, w_dev) if n_dev else None

        prev = np.array([np.nan if v is None else float(v) for v in prev_win])
        after_loss = g[(prev == 0) & ~np.isnan(g)]
        after_win = g[(prev == 1) & ~np.isnan(g)]
        tilt = b_tilt = None
        diff = np.nan
        if len(after_loss) >= 3 and len(after_win) >= 3:
            diff = float(after_loss.mean() - after_win.mean())
            var = cfg.tilt_scale ** 2 * (after_loss.var(ddof=1) / len(after_loss)
                                         + after_win.var(ddof=1) / len(after_win))
            wt = tau ** 2 / (tau ** 2 + var)
            tilt = float(np.clip(50 + wt * cfg.tilt_scale * diff, 0, 100))
            if B:
                il = rng.integers(0, len(after_loss), (B, len(after_loss)))
                iw = rng.integers(0, len(after_win), (B, len(after_win)))
                d = after_loss[il].mean(1) - after_win[iw].mean(1)
                b_tilt = np.clip(50 + wt * cfg.tilt_scale * d, 0, 100)

        tw = cfg.tilt_weight if tilt is not None else 0.0
        score = (1 - tw) * s_dev + tw * (tilt if tilt is not None else 0.0)
        bs = None
        if b_dev is not None:
            bs = (1 - tw) * b_dev + (tw * b_tilt if b_tilt is not None else 0.0)

        peer_sd = sum(self.peer_sd.get(r, np.nan) for r in roles if r) / max(
            sum(1 for r in roles if r), 1)
        subs = [SubScore("steadiness", "Game-to-game steadiness", float(s_dev),
                         n_dev, float(gm.std(ddof=1)) if len(gm) > 1 else np.nan,
                         float(peer_sd), 1 - tw)]
        if tilt is not None:
            subs.append(SubScore("tilt", "After a loss vs after a win",
                                 tilt, len(after_loss) + len(after_win), diff,
                                 0.0, tw))
        qlo, qhi = (1 - cfg.ci) / 2, 1 - (1 - cfg.ci) / 2
        ax = self._axis("consistency", score, raw_dev, n_dev, bs, qlo, qhi,
                        subs)
        return ax, bs


def fmt_score(a: AxisScore) -> str:
    if math.isnan(a.score):
        return "—"
    if math.isnan(a.lo):
        return "%.0f" % a.score
    return "%.0f [%.0f–%.0f]" % (a.score, a.lo, a.hi)
