"""Settings: API key, data directory and rate limits.

Lookup order for every setting: process environment, then ``.env`` (in the
current directory or the project root). The API key additionally falls back
to DRAFT's ``DRAFT_RIOT_API_KEY`` (environment, then the Windows user
environment in the registry, like DRAFT does), so both tools can share a key.
The key is only ever read at runtime — it is never written into code.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RATE_LIMITS: tuple[tuple[int, float], ...] = ((20, 1.0), (100, 120.0))


def parse_dotenv(text: str) -> dict[str, str]:
    """Minimal KEY=VALUE parser: comments, blank lines, optional quotes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    merged: dict[str, str] = {}
    paths = [PROJECT_ROOT / ".env", Path.cwd() / ".env"]
    if getattr(sys, "frozen", False):          # Review.exe: .env beside it
        exe_dir = Path(sys.executable).resolve().parent
        paths += [exe_dir / ".env", exe_dir.parent / ".env",
                  exe_dir.parent.parent / ".env"]   # dist/Review/ -> project
    for p in paths:
        try:
            merged.update(parse_dotenv(p.read_text(encoding="utf-8")))
        except OSError:
            pass
    return merged


def setting(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    v = _dotenv().get(name, "").strip()
    return v or default


def _windows_user_env(name: str) -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return str(winreg.QueryValueEx(h, name)[0]).strip() or None
    except Exception:
        return None


KEY_FILE = "api_key.txt"      # in data_dir(): a key pasted in Settings


def saved_key() -> str | None:
    """The key pasted in Settings (wins over a bundled .env key, which may
    be an expired 24-hour development key)."""
    try:
        k = (data_dir() / KEY_FILE).read_text(encoding="utf-8").strip()
        return k or None
    except OSError:
        return None


def save_key(key: str | None) -> None:
    """Store (or with None/'' remove) the Settings key."""
    p = data_dir() / KEY_FILE
    if key and key.strip():
        p.write_text(key.strip(), encoding="utf-8")
    else:
        try:
            p.unlink()
        except OSError:
            pass


def built_in_key() -> str | None:
    """The key compiled into a release build by packaging/build.py
    (review/_bundled_key.py exists only while that build runs)."""
    try:
        from . import _bundled_key as b
        if hasattr(b, "KEY"):                      # plain (tests)
            return (b.KEY or "").strip() or None
        import base64
        pad, masked = base64.b85decode(b.P), base64.b85decode(b.M)
        key = bytes(x ^ y for x, y in zip(masked, pad)).decode()
        return key.strip() or None
    except Exception:
        return None


def key_source() -> str | None:
    """Where api_key() gets its key: 'env', 'settings', 'built_in',
    'dotenv' or None — so an error can say whose key stopped working."""
    if os.environ.get("REVIEW_RIOT_API_KEY", "").strip():
        return "env"
    if saved_key():
        return "settings"
    if built_in_key():
        return "built_in"
    if (setting("REVIEW_RIOT_API_KEY") or setting("DRAFT_RIOT_API_KEY")
            or _windows_user_env("DRAFT_RIOT_API_KEY")):
        return "dotenv"
    return None


def api_key() -> str | None:
    """Lookup order: REVIEW_RIOT_API_KEY in the environment, the key saved
    in Settings, the key built into a release exe, a .env (project, or next
    to Review.exe), then DRAFT's key."""
    env = os.environ.get("REVIEW_RIOT_API_KEY", "").strip()
    return (env or saved_key() or built_in_key()
            or setting("REVIEW_RIOT_API_KEY")
            or setting("DRAFT_RIOT_API_KEY")
            or _windows_user_env("DRAFT_RIOT_API_KEY"))


def parse_rate_limits(spec: str) -> tuple[tuple[int, float], ...]:
    """'20:1,100:120' -> ((20, 1.0), (100, 120.0))."""
    windows = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        n, _, secs = part.partition(":")
        windows.append((int(n), float(secs)))
    if not windows:
        raise ValueError("empty rate limit spec")
    return tuple(windows)


def rate_limits() -> tuple[tuple[int, float], ...]:
    spec = setting("REVIEW_RATE_LIMITS")
    return parse_rate_limits(spec) if spec else DEFAULT_RATE_LIMITS


def rate_limits_configured() -> bool:
    """True when the user pinned limits explicitly (then we don't adapt)."""
    return setting("REVIEW_RATE_LIMITS") is not None


def data_dir() -> Path:
    custom = setting("REVIEW_DATA_DIR")
    if custom:
        p = Path(custom)
    elif os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "REVIEW"
    else:
        p = Path.home() / ".review"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "review.sqlite"
