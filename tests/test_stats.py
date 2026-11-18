import math

import numpy as np
import pytest

from review.analysis import stats


def test_wilson_known_values():
    lo, hi = stats.wilson(50, 100, z=1.959963984540054)
    assert lo == pytest.approx(0.4038, abs=1e-3)
    assert hi == pytest.approx(0.5962, abs=1e-3)
    lo, hi = stats.wilson(0, 10)
    assert lo == 0 and 0.1 < hi < 0.3          # never a degenerate [0, 0]


def test_t_quantile():
    assert stats.t_quantile(0.975, 10) == pytest.approx(2.228, abs=0.01)
    assert stats.t_quantile(0.975, 100) == pytest.approx(1.984, abs=0.005)
    assert stats._norm_ppf(0.995) == pytest.approx(2.5758, abs=1e-3)


def test_mean_se_ignores_nan():
    m = stats.mean_se([1, 2, 3, float("nan")])
    assert m.n == 3 and m.mean == 2 and m.se == pytest.approx(1 / math.sqrt(3))


def test_cohen_d_sign_and_size():
    rng = np.random.default_rng(1)
    a, b = rng.normal(1, 1, 2000), rng.normal(0, 1, 2000)
    e = stats.cohen_d(a, b)
    assert e.d == pytest.approx(1.0, abs=0.08)
    assert e.lo < e.d < e.hi
    assert stats.cohen_d(b, a).d < 0


def test_percentile_midrank():
    ref = np.sort(np.array([1., 2., 3., 4.]))
    p = stats.percentile_in(ref, np.array([0, 2.5, 5, 2, np.nan]))
    assert p[0] == 0 and p[1] == 50 and p[2] == 100
    assert p[3] == pytest.approx(37.5)        # tie counts half
    assert math.isnan(p[4])
    allsame = stats.percentile_in(np.array([3., 3., 3.]), np.array([3.]))
    assert allsame[0] == 50


def test_shrink_weight():
    assert stats.shrink_weight(28.9 ** 2, 6, 10) < 0.5     # few games: shrink
    assert stats.shrink_weight(28.9 ** 2, 500, 10) > 0.85  # many: trust data
    assert stats.shrink_weight(float("nan"), 10, 10) == 0


def test_ols_slope_detects_line_and_noise():
    rng = np.random.default_rng(3)
    y = 0.05 * np.arange(100) + rng.normal(0, 1, 100)
    s = stats.ols_slope(y)
    assert s.lo > 0 and s.lo < 0.05 < s.hi
    flat = stats.ols_slope(rng.normal(0, 1, 100))
    assert flat.lo < 0 < flat.hi


def test_rolling_mean():
    r = stats.rolling_mean([1, 2, 3, 4], 2)
    assert [(i, m) for i, m, _, _ in r] == [(1, 1.5), (2, 2.5), (3, 3.5)]
