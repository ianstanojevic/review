"""Sliding-window rate limiter with several simultaneous windows.

Riot applies app limits per routing value (euw1, europe, …), so each host is
its own bucket. A request may go out only if EVERY window (e.g. 20/1 s AND
100/2 min) has room. A 429 with Retry-After blocks the bucket until then.
Thread-safe; waiting happens outside the lock and can be cancelled.
"""
from __future__ import annotations

import bisect
import threading
import time
from typing import Callable, Sequence

from .errors import Cancelled

Windows = Sequence[tuple[int, float]]


class _Bucket:
    __slots__ = ("stamps", "blocked_until")

    def __init__(self):
        self.stamps: list[float] = []   # send times, ascending
        self.blocked_until = 0.0


class RateLimiter:
    def __init__(self, windows: Windows, *, margin: float = 0.25,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._clock, self._sleep = clock, sleep
        self.margin = margin   # seconds added to each window (clock skew/latency)
        self._held_until = 0.0  # when the current wait ends (for the UI)
        self.set_windows(windows)

    # -- configuration --------------------------------------------------
    @property
    def windows(self) -> tuple[tuple[int, float], ...]:
        return self._windows

    def set_windows(self, windows: Windows) -> None:
        ws = tuple(sorted((int(n), float(s)) for n, s in windows))
        if not ws or any(n <= 0 or s <= 0 for n, s in ws):
            raise ValueError("invalid rate limit windows: %r" % (windows,))
        with self._lock:
            self._windows = ws
            self._span = max(s for _, s in ws) + self.margin

    def sustained_rate(self) -> float:
        """Requests/second the tightest window allows long-term (for ETAs)."""
        return min(n / s for n, s in self._windows)

    # -- core -----------------------------------------------------------
    def _wait_needed(self, b: _Bucket, now: float) -> float:
        wait = b.blocked_until - now
        stamps = b.stamps
        cut = bisect.bisect_right(stamps, now - self._span)
        if cut:
            del stamps[:cut]
        for limit, span in self._windows:
            in_window = len(stamps) - bisect.bisect_right(
                stamps, now - span - self.margin)
            if in_window >= limit:
                # the window frees up when the oldest relevant stamp expires
                oldest = stamps[len(stamps) - limit]
                wait = max(wait, oldest + span + self.margin - now)
        return wait

    def acquire(self, bucket: str,
                cancel: threading.Event | None = None) -> None:
        """Block until a request to ``bucket`` may be sent, then record it."""
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            with self._lock:
                b = self._buckets.setdefault(bucket, _Bucket())
                now = self._clock()
                wait = self._wait_needed(b, now)
                if wait <= 0:
                    b.stamps.append(now)
                    return
                self._held_until = max(self._held_until, now + wait)
            self._pause(min(wait, 5.0), cancel)   # re-check periodically

    def waiting_for(self) -> float:
        """Seconds until held-back requests may go out (0 = not waiting).
        Lets the UI say "waiting for Riot's rate limit" instead of looking
        frozen during the ~2-minute pauses of a personal key."""
        with self._lock:
            return max(0.0, self._held_until - self._clock())

    def penalize(self, bucket: str, seconds: float) -> None:
        """Riot said 429 + Retry-After: hold the whole bucket."""
        with self._lock:
            b = self._buckets.setdefault(bucket, _Bucket())
            b.blocked_until = max(b.blocked_until, self._clock() + seconds)

    def _pause(self, seconds: float, cancel: threading.Event | None) -> None:
        if cancel is not None:
            if cancel.wait(seconds):
                raise Cancelled()
        else:
            self._sleep(seconds)
