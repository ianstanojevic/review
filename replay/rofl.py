"""Replay files (.rofl) on disk.

A .rofl isn't a video: it's the game's network data, which only the League
client can play back, and only on the patch it was recorded on. What we can
read ourselves is the header (game version) and the JSON metadata at the end
(length, per-player stats). Files are named ``EUW1-8050970813.rofl``, which
maps straight to the Riot API match ID ``EUW1_8050970813``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^([A-Z0-9]+)-(\d+)\.rofl$", re.I)
_VERSION = re.compile(rb"(\d{1,3}\.\d{1,3}\.\d+\.\d+)")


@dataclass
class RoflFile:
    path: Path
    match_id: str
    game_id: int
    version: str | None
    size: int
    mtime: float

    @property
    def patch(self) -> str | None:
        if not self.version:
            return None
        return ".".join(self.version.split(".")[:2])


def default_dir() -> Path:
    return Path.home() / "Documents" / "League of Legends" / "Replays"


def read_version(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        return None
    m = _VERSION.search(head)
    return m.group(1).decode() if m else None


def read_metadata(path: Path) -> dict | None:
    """{'gameLength': ms, 'stats': [per-player dicts]} or None."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(size - 200_000, 0))
            tail = f.read()
    except OSError:
        return None
    i = tail.rfind(b'{"gameLength"')
    if i < 0:
        return None
    try:
        d, _ = json.JSONDecoder().raw_decode(tail[i:].decode("utf-8",
                                                             "replace"))
        stats = json.loads(d.get("statsJson") or "[]")
    except ValueError:
        return None
    return {"gameLength": d.get("gameLength"), "stats": stats}


def scan(folder: Path | None = None) -> dict[str, RoflFile]:
    """match_id -> file, for every replay in the folder."""
    folder = Path(folder) if folder else default_dir()
    out: dict[str, RoflFile] = {}
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return out
    for de in entries:
        m = _NAME.match(de.name)
        if not m or not de.is_file():
            continue
        p = Path(de.path)
        st = de.stat()
        mid = "%s_%s" % (m.group(1).upper(), m.group(2))
        out[mid] = RoflFile(p, mid, int(m.group(2)), read_version(p),
                            st.st_size, st.st_mtime)
    return out
