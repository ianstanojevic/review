import json

import pytest

from conftest import FakeClock
from review.riot_client import (InvalidKey, MissingKey, NotFound,
                                RateLimiter, RiotClient, ServiceUnavailable,
                                get_region, parse_limit_header)

EUW = get_region("EUW")


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        status, hdrs, body = r
        return status, hdrs, json.dumps(body).encode()


def client(responses, **kw):
    clk = FakeClock()
    lim = RateLimiter([(20, 1.0), (100, 120.0)], clock=clk, sleep=clk.sleep)
    t = FakeTransport(responses)
    return RiotClient("RGAPI-test", lim, transport=t, sleep=clk.sleep,
                      **kw), t, clk


def test_ok_and_routing():
    c, t, _ = client([(200, {}, {"puuid": "P"})])
    assert c.account_by_riot_id(EUW, "flint gnuggaren", "ian") == {"puuid": "P"}
    url, headers = t.calls[0]
    assert url.startswith("https://europe.api.riotgames.com/riot/account/v1/")
    assert "flint%20gnuggaren/ian" in url
    assert headers["X-Riot-Token"] == "RGAPI-test"


def test_league_uses_platform_host():
    c, t, _ = client([(200, {}, [])])
    c.league_entries(EUW, "P")
    assert t.calls[0][0].startswith("https://euw1.api.riotgames.com/lol/league")


def test_match_ids_params():
    c, t, _ = client([(200, {}, ["EUW1_2", "EUW1_1"])])
    assert c.match_ids(EUW, "P", queue=420, start=100, count=100) == [
        "EUW1_2", "EUW1_1"]
    assert "queue=420" in t.calls[0][0] and "start=100" in t.calls[0][0]
    assert "startTime" not in t.calls[0][0]


def test_429_retry_after_penalizes_and_retries():
    c, t, clk = client([
        (429, {"retry-after": "4", "x-rate-limit-type": "application"}, {}),
        (200, {}, {"ok": 1}),
    ])
    assert c.match(EUW, "EUW1_1") == {"ok": 1}
    assert clk.t == pytest.approx(1004.0, abs=0.01)
    assert c.requests_sent == 2


def test_5xx_backs_off_then_succeeds():
    c, t, clk = client([(503, {}, {}), (502, {}, {}), (200, {}, {"ok": 1})])
    assert c.match(EUW, "EUW1_1") == {"ok": 1}
    assert len(clk.slept) == 2


def test_gives_up_when_down():
    c, _, _ = client([(503, {}, {})] * 6)
    with pytest.raises(ServiceUnavailable):
        c.match(EUW, "EUW1_1")


def test_network_error_retried():
    c, _, _ = client([OSError("boom"), (200, {}, {"ok": 1})])
    assert c.match(EUW, "x") == {"ok": 1}


@pytest.mark.parametrize("status", [401, 403])
def test_bad_key(status):
    c, _, _ = client([(status, {}, {})])
    with pytest.raises(InvalidKey):
        c.match(EUW, "x")


def test_404():
    c, _, _ = client([(404, {}, {})])
    with pytest.raises(NotFound):
        c.match(EUW, "x")


def test_missing_key():
    with pytest.raises(MissingKey):
        RiotClient(None, RateLimiter([(1, 1)]))


def test_adapts_to_app_rate_limit_header():
    c, _, _ = client([(200, {"x-app-rate-limit": "500:10,30000:600"}, {})])
    c.match(EUW, "x")
    assert c.limiter.windows == ((500, 10.0), (30000, 600.0))


def test_no_adapt_when_pinned():
    c, _, _ = client([(200, {"x-app-rate-limit": "500:10"}, {})],
                     adapt_limits=False)
    c.match(EUW, "x")
    assert c.limiter.windows == ((20, 1.0), (100, 120.0))


def test_parse_limit_header():
    assert parse_limit_header("20:1,100:120") == ((20, 1.0), (100, 120.0))
    assert parse_limit_header("garbage") == ()


def test_trouble_is_flagged_then_cleared():
    c, _, _ = client([OSError("down"), (503, {}, {}), (200, {}, {"ok": 1})])
    seen = []
    orig = c._pause
    c._pause = lambda s, cancel: (seen.append(c.trouble_since), orig(s, cancel))
    assert c.match(EUW, "x") == {"ok": 1}
    assert seen and all(t is not None for t in seen)
    assert c.trouble_since is None


class RawTransport(FakeTransport):
    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        return self.responses.pop(0)


def test_unreadable_200_is_retried_not_crashed():
    clk = FakeClock()
    lim = RateLimiter([(20, 1.0)], clock=clk, sleep=clk.sleep)
    t = RawTransport([(200, {}, b"<html>captive portal</html>"),
                      (200, {}, b'{"ok": 1}')])
    c = RiotClient("RGAPI-test", lim, transport=t, sleep=clk.sleep)
    assert c.match(EUW, "x") == {"ok": 1}
    t2 = RawTransport([(200, {}, b"<html>") for _ in range(6)])
    c2 = RiotClient("RGAPI-test", lim, transport=t2, sleep=clk.sleep)
    with pytest.raises(ServiceUnavailable):
        c2.match(EUW, "x")
