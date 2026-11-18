"""Small statistics toolkit (numpy only — no scipy, keeps the .exe small).

Conventions: intervals are 90 % unless stated; NaN = "not available" and is
ignored by every aggregate.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np

Z90 = 1.6448536269514722
Z95 = 1.959963984540054


def arr(xs) -> np.ndarray:
    a = np.asarray(xs, dtype=float)
    return a[~np.isnan(a)]


def norm_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2))


def p_two_sided(z: float) -> float:
    return 2 * (1 - norm_cdf(abs(z)))


def t_quantile(p: float, df: float) -> float:
    """Student-t quantile via a Cornish–Fisher expansion of the normal
    quantile (accurate to ~1e-3 for df ≥ 5, which is all we use)."""
    z = {0.95: Z90, 0.975: Z95}.get(p)
    if z is None:
        z = _norm_ppf(p)
    if df <= 0 or math.isinf(df):
        return z
    g1 = (z ** 3 + z) / 4
    g2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / 96
    g3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / 384
    return z + g1 / df + g2 / df ** 2 + g3 / df ** 3


def _norm_ppf(p: float) -> float:
    # Acklam's rational approximation
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - lo:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
        (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ------------------------------------------------------------------ rates

def wilson(wins: float, n: int, z: float = Z90) -> tuple[float, float]:
    """Wilson score interval for a proportion (default 90 %)."""
    if n <= 0:
        return 0.0, 1.0
    p = min(max(wins / n, 0.0), 1.0)   # robust to non-binary "wins"
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Rate:
    wins: int
    n: int

    @property
    def p(self) -> float:
        return self.wins / self.n if self.n else float("nan")

    @property
    def ci(self) -> tuple[float, float]:
        return wilson(self.wins, self.n)


# ------------------------------------------------------------------ means

@dataclass
class Mean:
    mean: float
    se: float
    n: int
    sd: float = float("nan")

    @property
    def lo(self) -> float:
        return self.mean - Z90 * self.se

    @property
    def hi(self) -> float:
        return self.mean + Z90 * self.se

    @property
    def half(self) -> float:
        return Z90 * self.se


def mean_se(xs) -> Mean:
    a = arr(xs)
    n = len(a)
    if n == 0:
        return Mean(float("nan"), float("nan"), 0)
    sd = float(a.std(ddof=1)) if n > 1 else float("nan")
    return Mean(float(a.mean()), sd / math.sqrt(n) if n > 1 else float("nan"),
                n, sd)


def nanmean_rows(m: np.ndarray, axis=1) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(m, axis=axis)


# ------------------------------------------------------------- comparison

@dataclass
class Effect:
    d: float
    lo: float
    hi: float
    mean_a: float
    mean_b: float
    n_a: int
    n_b: int


def cohen_d(a, b, z: float = Z90) -> Effect:
    """Cohen's d (a − b, pooled SD) with its large-sample CI."""
    a, b = arr(a), arr(b)
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return Effect(float("nan"), float("nan"), float("nan"),
                      float(a.mean()) if na else float("nan"),
                      float(b.mean()) if nb else float("nan"), na, nb)
    sp = math.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1))
                   / (na + nb - 2))
    d = (a.mean() - b.mean()) / sp if sp > 0 else 0.0
    se = math.sqrt((na + nb) / (na * nb) + d * d / (2 * (na + nb)))
    return Effect(float(d), float(d - z * se), float(d + z * se),
                  float(a.mean()), float(b.mean()), na, nb)


def welch_z(a, b) -> float:
    a, b = arr(a), arr(b)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else 0.0


# ---------------------------------------------------------------- trends

@dataclass
class Slope:
    slope: float      # per game
    lo: float
    hi: float
    n: int


def ols_slope(y, conf: float = 0.95) -> Slope:
    """OLS slope of y against its index, with a t-based CI."""
    y = np.asarray(y, dtype=float)
    x = np.arange(len(y), dtype=float)
    ok = ~np.isnan(y)
    x, y = x[ok], y[ok]
    n = len(y)
    if n < 5:
        return Slope(float("nan"), float("nan"), float("nan"), n)
    xm = x - x.mean()
    sxx = float((xm ** 2).sum())
    b = float((xm * (y - y.mean())).sum() / sxx)
    resid = y - (y.mean() + b * xm)
    s2 = float((resid ** 2).sum() / (n - 2))
    se = math.sqrt(s2 / sxx)
    t = t_quantile(1 - (1 - conf) / 2, n - 2)
    return Slope(b, b - t * se, b + t * se, n)


def rolling_mean(y, window: int) -> list[tuple[int, float, float, int]]:
    """[(end_index, mean, se, n)] over a trailing window (NaNs skipped)."""
    y = np.asarray(y, dtype=float)
    out = []
    for i in range(window - 1, len(y)):
        m = mean_se(y[i - window + 1:i + 1])
        out.append((i, m.mean, m.se, m.n))
    return out


# ------------------------------------------------------------ percentiles

def percentile_in(ref_sorted: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Mid-rank percentile (0..100) of each x within a sorted reference.
    Ties count half, so a value equal to everyone scores 50."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if len(ref_sorted) == 0:
        return out
    ok = ~np.isnan(x)
    lo = np.searchsorted(ref_sorted, x[ok], "left")
    hi = np.searchsorted(ref_sorted, x[ok], "right")
    out[ok] = 100.0 * (lo + hi) / (2.0 * len(ref_sorted))
    return out


# -------------------------------------------------------------- shrinkage

def shrink_weight(var_per_game: float, n: int, prior_sd: float) -> float:
    """Bayesian shrinkage weight τ² / (τ² + σ²/n): the share of the observed
    deviation from the prior mean that we believe is signal."""
    if n <= 0 or math.isnan(var_per_game):
        return 0.0
    tau2 = prior_sd ** 2
    return tau2 / (tau2 + var_per_game / n)
