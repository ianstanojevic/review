"""Data Dragon static data (no API key needed), cached on disk.

Only what the analysis needs: the set of legendary item IDs, for "minute of
first completed legendary". Fails soft: without network and without a cached
copy it returns an empty set and item timing is simply unavailable.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from . import config

DDRAGON = "https://ddragon.leagueoflegends.com"
MIN_LEGENDARY_GOLD = 2200


def _dir() -> Path:
    p = config.data_dir() / "ddragon"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "review"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def latest_version(max_age_h: float = 24,
                   offline: bool = False) -> str | None:
    """``offline=True`` never touches the network (UI thread): it returns
    the last known version, however old, or None."""
    f = _dir() / "versions.json"
    if f.exists() and (offline or time.time() - f.stat().st_mtime
                       < max_age_h * 3600):
        return json.loads(f.read_text())[0]
    if offline:
        return None
    try:
        versions = _get(DDRAGON + "/api/versions.json")
        f.write_text(json.dumps(versions[:5]))
        return versions[0]
    except Exception:
        return json.loads(f.read_text())[0] if f.exists() else None


def items(version: str | None = None, offline: bool = False) -> dict:
    version = version or latest_version(offline=offline)
    if not version:
        return {}
    f = _dir() / ("item_%s.json" % version)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    if offline:
        return {}
    try:
        data = _get("%s/cdn/%s/data/en_US/item.json" % (DDRAGON, version))["data"]
    except Exception:
        return {}
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def is_legendary(it: dict) -> bool:
    g = it.get("gold", {})
    return bool(it.get("maps", {}).get("11") and g.get("purchasable")
                and it.get("inStore", True) and not it.get("into")
                and g.get("total", 0) >= MIN_LEGENDARY_GOLD
                and "Boots" not in it.get("tags", [])
                and "Consumable" not in it.get("tags", [])
                and not it.get("requiredChampion")
                and not it.get("requiredAlly"))


def legendary_ids(version: str | None = None,
                  offline: bool = False) -> frozenset[int]:
    return frozenset(int(i) for i, it in items(version, offline).items()
                     if is_legendary(it))


# ------------------------------------------------------------- champions

def champions(version: str | None = None,
              offline: bool = False) -> dict[str, dict]:
    """championName (as in match-v5, lower-cased) -> {name, image}."""
    version = version or latest_version(offline=offline)
    if not version:
        return {}
    f = _dir() / ("champion_%s.json" % version)
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
    elif offline:
        return {}
    else:
        try:
            data = _get("%s/cdn/%s/data/en_US/champion.json"
                        % (DDRAGON, version))["data"]
        except Exception:
            return {}
        f.write_text(json.dumps(data), encoding="utf-8")
    return {k.lower(): {"name": v["name"], "image": v["image"]["full"]}
            for k, v in data.items()}


def champion_name(champ: str) -> str:
    info = _champ_cache().get((champ or "").lower())
    return info["name"] if info else champ


_CHAMPS: dict | None = None


def _champ_cache() -> dict:
    """Never blocks on the network: names are read from disk; the app's
    background loader calls ``refresh_champions()`` for the online copy.
    Until then an unknown champion simply shows its internal name."""
    global _CHAMPS
    if _CHAMPS is None:
        try:
            _CHAMPS = champions(offline=True)
        except Exception:
            _CHAMPS = {}
    return _CHAMPS


def refresh_champions() -> None:
    """Online refresh (call from a worker thread)."""
    global _CHAMPS
    data = champions()
    if data:
        _CHAMPS = data


def champion_icon_local(champ: str) -> Path | None:
    """The icon's file if it is already cached on disk (no network)."""
    p = _dir() / "icons" / ("%s.png" % (champ or "").lower())
    return p if p.exists() else None


def champion_icon(champ: str) -> Path | None:
    """Local PNG for a champion icon, downloaded once from Data Dragon."""
    d = _dir() / "icons"
    d.mkdir(exist_ok=True)
    p = d / ("%s.png" % (champ or "").lower())
    if p.exists():
        return p
    info = _champ_cache().get((champ or "").lower())
    ver = latest_version()
    if not info or not ver:
        return None
    try:
        req = urllib.request.Request("%s/cdn/%s/img/champion/%s" % (
            DDRAGON, ver, info["image"]), headers={"User-Agent": "review"})
        with urllib.request.urlopen(req, timeout=15) as r:
            p.write_bytes(r.read())
        return p
    except Exception:
        return None


def minimap(offline: bool = False) -> Path | None:
    """Summoner's Rift minimap (map 11) from Data Dragon."""
    p = _dir() / "map11.png"
    if p.exists():
        return p
    if offline:
        return None
    ver = latest_version()
    if not ver:
        return None
    try:
        req = urllib.request.Request("%s/cdn/%s/img/map/map11.png" % (
            DDRAGON, ver), headers={"User-Agent": "review"})
        with urllib.request.urlopen(req, timeout=15) as r:
            p.write_bytes(r.read())
        return p
    except Exception:
        return None
