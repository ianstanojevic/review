"""Riot API client: account-v1, league-v4, match-v5 (+ timelines).

Every request passes the shared RateLimiter. Retries: 429 honours
Retry-After (and blocks the bucket); 5xx and network errors back off
exponentially with jitter. 401/403 -> InvalidKey, 404 -> NotFound.
"""
from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .. import __version__
from .errors import (Cancelled, InvalidKey, MissingKey, NotFound,
                     RateLimited, RiotApiError, ServiceUnavailable)
from .limiter import RateLimiter
from .routing import Region

# transport(url, headers, timeout) -> (status, lower-cased headers, body)
Transport = Callable[[str, dict, float], tuple[int, dict, bytes]]


def urllib_transport(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, hdrs, body


def parse_limit_header(value: str) -> tuple[tuple[int, float], ...]:
    """'20:1,100:120' -> ((20, 1.0), (100, 120.0)); () if malformed."""
    out = []
    try:
        for part in value.split(","):
            n, s = part.strip().split(":")
            out.append((int(n), float(s)))
    except (ValueError, AttributeError):
        return ()
    return tuple(out)


class RiotClient:
    def __init__(self, api_key: str | None, limiter: RateLimiter, *,
                 transport: Transport = urllib_transport,
                 timeout: float = 20.0, max_attempts: int = 6,
                 adapt_limits: bool = True,
                 sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise MissingKey()
        self._key = api_key
        self.limiter = limiter
        self._transport = transport
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._adapt = adapt_limits
        self._sleep = sleep
        self._count_lock = threading.Lock()
        self.requests_sent = 0

    # -- plumbing -------------------------------------------------------
    def _pause(self, seconds: float, cancel: threading.Event | None):
        if cancel is not None:
            if cancel.wait(seconds):
                raise Cancelled()
        else:
            self._sleep(seconds)

    def _learn_limits(self, headers: dict) -> None:
        if not self._adapt:
            return
        ws = parse_limit_header(headers.get("x-app-rate-limit", ""))
        if ws and tuple(sorted(ws)) != self.limiter.windows:
            self.limiter.set_windows(ws)

    def get(self, host: str, path: str, params: dict | None = None,
            cancel: threading.Event | None = None, raw: bool = False) -> Any:
        """``raw=True`` returns the response body bytes unparsed."""
        url = "https://%s.api.riotgames.com%s" % (host, path)
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        headers = {"X-Riot-Token": self._key,
                   "User-Agent": "review/%s" % __version__,
                   "Accept": "application/json"}
        last_err: RiotApiError = ServiceUnavailable()
        for attempt in range(self._max_attempts):
            self.limiter.acquire(host, cancel)
            with self._count_lock:
                self.requests_sent += 1
            try:
                status, hdrs, body = self._transport(url, headers,
                                                     self._timeout)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last_err = ServiceUnavailable(type(e).__name__)
                self._pause(self._backoff(attempt), cancel)
                continue
            if status == 200:
                self._learn_limits(hdrs)
                return body if raw else json.loads(body.decode("utf-8"))
            if status == 404:
                raise NotFound()
            if status in (401, 403):
                raise InvalidKey(status)
            if status == 429:
                last_err = RateLimited()
                retry_after = hdrs.get("retry-after")
                if retry_after:
                    wait = float(retry_after)
                    if hdrs.get("x-rate-limit-type") in ("application",
                                                         "method"):
                        self.limiter.penalize(host, wait)
                        continue          # acquire() now waits for us
                    self._pause(wait, cancel)
                else:                     # service-level 429: back off
                    self._pause(self._backoff(attempt), cancel)
                continue
            if status >= 500:
                last_err = ServiceUnavailable("HTTP %d" % status)
                self._pause(self._backoff(attempt), cancel)
                continue
            raise RiotApiError("Riot API returned HTTP %d" % status, status)
        raise last_err

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 1.0 * 2 ** attempt) + random.uniform(0, 0.5)

    # -- endpoints ------------------------------------------------------
    def account_by_riot_id(self, region: Region, game_name: str, tag: str,
                           cancel=None) -> dict:
        path = "/riot/account/v1/accounts/by-riot-id/%s/%s" % (
            urllib.parse.quote(game_name, safe=""),
            urllib.parse.quote(tag, safe=""))
        return self.get(region.account_host, path, cancel=cancel)

    def league_entries(self, region: Region, puuid: str,
                       cancel=None) -> list[dict]:
        return self.get(region.platform,
                        "/lol/league/v4/entries/by-puuid/%s" % puuid,
                        cancel=cancel) or []

    def match_ids(self, region: Region, puuid: str, *, queue: int | None,
                  start: int = 0, count: int = 100,
                  start_time: int | None = None, cancel=None) -> list[str]:
        return self.get(
            region.regional,
            "/lol/match/v5/matches/by-puuid/%s/ids" % puuid,
            {"queue": queue, "start": start, "count": min(count, 100),
             "startTime": start_time},
            cancel=cancel) or []

    def match(self, region: Region, match_id: str, cancel=None) -> dict:
        return self.get(region.regional,
                        "/lol/match/v5/matches/%s" % match_id, cancel=cancel)

    def timeline(self, region: Region, match_id: str, cancel=None,
                 raw: bool = False):
        """``raw=True``: the JSON bytes as received. Timelines are ~0.5 MB;
        parsing them in a download thread holds Python's GIL for tens of
        ms and starves the UI thread, and the cache only needs the bytes."""
        return self.get(region.regional,
                        "/lol/match/v5/matches/%s/timeline" % match_id,
                        cancel=cancel, raw=raw)

    def apex_league(self, region: Region, tier: str,
                    queue: str = "RANKED_SOLO_5x5", cancel=None) -> list[dict]:
        """Every player in MASTER / GRANDMASTER / CHALLENGER (with puuid)."""
        path = "/lol/league/v4/%sleagues/by-queue/%s" % (tier.lower(), queue)
        return (self.get(region.platform, path, cancel=cancel)
                or {}).get("entries", [])

    def league_page(self, region: Region, tier: str, division: str,
                    page: int = 1, queue: str = "RANKED_SOLO_5x5",
                    cancel=None) -> list[dict]:
        """One page (~205 players) of a tier/division, e.g. DIAMOND I."""
        return self.get(region.platform, "/lol/league/v4/entries/%s/%s/%s"
                        % (queue, tier.upper(), division), {"page": page},
                        cancel=cancel) or []
