import threading

import pytest

from conftest import FakeClock
from review.riot_client import Cancelled, RateLimiter


def make(windows=((20, 1.0), (100, 120.0)), margin=0.0):
    clk = FakeClock()
    return RateLimiter(windows, margin=margin, clock=clk, sleep=clk.sleep), clk


def test_short_window_blocks_21st_request():
    lim, clk = make()
    for _ in range(20):
        lim.acquire("europe")
    assert clk.slept == []
    lim.acquire("europe")
    assert clk.t == pytest.approx(1001.0)   # waited one second


def test_long_window_blocks_101st_request():
    lim, clk = make()
    for _ in range(100):
        lim.acquire("europe")
    start_of_burst = 1000.0
    lim.acquire("europe")
    # the first stamp in the burst must leave the 120 s window
    assert clk.t >= start_of_burst + 120.0
    # and never more than 20 within any 1 s span
    # (100 requests needed ≥ 4 s of 1 s windows)


def test_both_windows_hold_over_long_run():
    lim, clk = make()
    stamps = []
    for _ in range(350):
        lim.acquire("europe")
        stamps.append(clk.t)
    for i in range(len(stamps)):
        in_1s = sum(1 for s in stamps[i:] if s < stamps[i] + 1.0)
        in_120 = sum(1 for s in stamps[i:] if s < stamps[i] + 120.0)
        assert in_1s <= 20 and in_120 <= 100


def test_buckets_are_independent():
    lim, clk = make()
    for _ in range(20):
        lim.acquire("europe")
    lim.acquire("euw1")
    assert clk.slept == []


def test_penalize_blocks_bucket():
    lim, clk = make()
    lim.penalize("europe", 7.0)
    lim.acquire("europe")
    assert clk.t == pytest.approx(1007.0)


def test_margin_extends_windows():
    lim, clk = make(windows=((1, 1.0),), margin=0.25)
    lim.acquire("x")
    lim.acquire("x")
    assert clk.t == pytest.approx(1001.25)


def test_set_windows_takes_effect():
    lim, clk = make(windows=((1, 10.0),))
    lim.acquire("x")
    lim.set_windows([(5, 10.0)])
    for _ in range(4):
        lim.acquire("x")
    assert clk.slept == []


def test_cancel_interrupts_wait():
    lim = RateLimiter([(1, 3600.0)])
    lim.acquire("x")
    ev = threading.Event()
    threading.Timer(0.1, ev.set).start()
    with pytest.raises(Cancelled):
        lim.acquire("x", cancel=ev)


def test_sustained_rate():
    lim, _ = make()
    assert lim.sustained_rate() == pytest.approx(100 / 120)


def test_waiting_for_reports_the_hold():
    t = [0.0]
    lim = RateLimiter([(2, 10.0)], margin=0.0, clock=lambda: t[0],
                      sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert lim.waiting_for() == 0
    lim.acquire("euw1")
    lim.acquire("euw1")
    # the third request has to wait until t=10; while blocked, the UI can
    # read how long is left
    seen = []
    lim._sleep = lambda s: (seen.append(lim.waiting_for()),
                            t.__setitem__(0, t[0] + s))
    lim.acquire("euw1")
    assert seen and abs(seen[0] - 10.0) < 1e-6
    assert lim.waiting_for() == 0
