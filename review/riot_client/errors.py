"""Error types. Each carries a short user-facing message for the UI/CLI —
raw tracebacks are never shown to the user."""
from __future__ import annotations


class ReviewError(Exception):
    """Base class: ``str(e)`` is safe to show to the user."""


class Cancelled(ReviewError):
    def __init__(self, msg: str = "Cancelled"):
        super().__init__(msg)


class RiotApiError(ReviewError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class MissingKey(RiotApiError):
    def __init__(self):
        super().__init__("No Riot API key yet. Get one at "
                         "developer.riotgames.com and paste it in ⚙ Settings "
                         "(top right).")


class InvalidKey(RiotApiError):
    def __init__(self, status: int):
        super().__init__("Riot rejected the API key (HTTP %d). Development "
                         "keys expire every 24 h — get a new one at "
                         "developer.riotgames.com and paste it in ⚙ Settings."
                         % status, status)


class NotFound(RiotApiError):
    def __init__(self, what: str = "Not found"):
        super().__init__(what, 404)


class PlayerNotFound(NotFound):
    pass


class RateLimited(RiotApiError):
    def __init__(self):
        super().__init__("Riot's rate limit kept rejecting requests. Try "
                         "again in a couple of minutes.", 429)


class ServiceUnavailable(RiotApiError):
    def __init__(self, detail: str = ""):
        super().__init__("Riot's API is unreachable or down right now"
                         + (" (%s)" % detail if detail else "") + ".")
