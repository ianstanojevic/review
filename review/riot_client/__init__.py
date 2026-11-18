"""Riot API access: routing, rate limiting and the HTTP client."""
from .. import config
from .client import RiotClient, parse_limit_header
from .errors import (Cancelled, InvalidKey, MissingKey, NotFound,
                     PlayerNotFound, RateLimited, ReviewError, RiotApiError,
                     ServiceUnavailable)
from .limiter import RateLimiter
from .routing import (PRIMARY_REGIONS, REGIONS, Region, get_region,
                      parse_riot_id)


_LIMITER: RateLimiter | None = None


def default_client() -> RiotClient:
    """Client wired to the configured key and rate limits. Every client
    shares ONE limiter: the player fetch, a Compare fetch and a benchmark
    build may run at the same time, and Riot's limit is per key."""
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter(config.rate_limits())
    return RiotClient(config.api_key(), _LIMITER,
                      adapt_limits=not config.rate_limits_configured())


__all__ = [
    "RiotClient", "RateLimiter", "Region", "REGIONS", "PRIMARY_REGIONS",
    "get_region", "parse_riot_id", "parse_limit_header", "default_client",
    "ReviewError", "RiotApiError", "Cancelled", "InvalidKey", "MissingKey",
    "NotFound", "PlayerNotFound", "RateLimited", "ServiceUnavailable",
]
