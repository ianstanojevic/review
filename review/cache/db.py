"""SQLite cache of raw match + timeline JSON, keyed by match ID.

* A match is fetched at most once — ever — and shared by every player in it
  (friends who duo share downloads). The ``participants`` index maps
  PUUID -> matches without decoding any JSON.
* JSON is stored zlib-compressed (timelines are ~0.5 MB raw, ~10x smaller).
* A timeline row with NULL data means Riot has no timeline for that match
  (404) — remembered so we don't keep asking.
* ``rank_snapshots`` gets one row per queue per sync; the Riot API has no LP
  history, so this is how an LP-over-time line builds up.

Thread-safe: one connection guarded by a lock.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,
    platform      TEXT,
    queue_id      INTEGER,
    game_creation INTEGER,       -- ms epoch
    game_duration INTEGER,       -- seconds
    game_version  TEXT,
    fetched_at    INTEGER,
    data          BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_matches_creation ON matches(game_creation);
CREATE TABLE IF NOT EXISTS timelines (
    match_id   TEXT PRIMARY KEY,
    fetched_at INTEGER,
    data       BLOB              -- NULL: Riot has no timeline
);
CREATE TABLE IF NOT EXISTS participants (
    match_id      TEXT NOT NULL,
    puuid         TEXT NOT NULL,
    team_id       INTEGER,
    team_position TEXT,
    champion_id   INTEGER,
    win           INTEGER,
    PRIMARY KEY (match_id, puuid)
);
CREATE INDEX IF NOT EXISTS ix_participants_puuid ON participants(puuid);
CREATE TABLE IF NOT EXISTS players (
    puuid       TEXT PRIMARY KEY,
    game_name   TEXT,
    tag_line    TEXT,
    region      TEXT,
    last_synced INTEGER
);
CREATE TABLE IF NOT EXISTS rank_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    puuid      TEXT NOT NULL,
    taken_at   INTEGER NOT NULL,  -- s epoch
    queue_type TEXT NOT NULL,     -- RANKED_SOLO_5x5 / RANKED_FLEX_SR
    tier       TEXT,
    division   TEXT,
    lp         INTEGER,
    wins       INTEGER,
    losses     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rank_puuid ON rank_snapshots(puuid, taken_at);
-- derived data (safe to delete; rebuilt on demand)
CREATE TABLE IF NOT EXISTS features (
    match_id TEXT PRIMARY KEY,
    version  TEXT NOT NULL,
    data     BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_games (
    region   TEXT NOT NULL,      -- EUW, NA, ...
    level    TEXT NOT NULL,      -- diamond | master
    match_id TEXT NOT NULL,
    added_at INTEGER,
    PRIMARY KEY (region, level, match_id)
);
CREATE TABLE IF NOT EXISTS analysis_cache (
    key     TEXT PRIMARY KEY,
    created INTEGER,
    data    BLOB NOT NULL
);
"""


def _pack(obj) -> bytes:
    return zlib.compress(json.dumps(obj, separators=(",", ":")).encode(), 6)


def _unpack(blob: bytes):
    return json.loads(zlib.decompress(blob))


@dataclass(frozen=True)
class RankSnapshot:
    taken_at: int
    queue_type: str
    tier: str | None
    division: str | None
    lp: int | None
    wins: int | None
    losses: int | None


class Cache:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.execute("INSERT OR IGNORE INTO meta VALUES "
                             "('schema_version', ?)", (str(SCHEMA_VERSION),))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- matches --------------------------------------------------------
    def put_match(self, match: dict) -> None:
        meta, info = match["metadata"], match["info"]
        mid = meta["matchId"]
        blob = _pack(match)            # compress outside the lock
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?)",
                (mid, info.get("platformId"), info.get("queueId"),
                 info.get("gameCreation"), info.get("gameDuration"),
                 info.get("gameVersion"), int(time.time()), blob))
            self._db.executemany(
                "INSERT OR REPLACE INTO participants VALUES (?,?,?,?,?,?)",
                [(mid, p["puuid"], p.get("teamId"), p.get("teamPosition"),
                  p.get("championId"), int(bool(p.get("win"))))
                 for p in info.get("participants", []) if p.get("puuid")])

    def get_match(self, match_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM matches WHERE match_id=?",
                                   (match_id,)).fetchone()
        return _unpack(row[0]) if row else None

    def put_timeline(self, match_id: str,
                     timeline: dict | bytes | None) -> None:
        """``None`` records that Riot has no timeline for this match.
        ``bytes`` (the raw JSON body) is stored without a parse/re-dump
        round trip; zlib releases the GIL while it compresses."""
        if timeline is None:
            blob = None
        elif isinstance(timeline, (bytes, bytearray)):
            blob = zlib.compress(bytes(timeline), 6)
        else:
            blob = _pack(timeline)
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO timelines VALUES (?,?,?)",
                (match_id, int(time.time()), blob))

    def get_timeline(self, match_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM timelines WHERE match_id=?",
                (match_id,)).fetchone()
        return _unpack(row[0]) if row and row[0] is not None else None

    def _present(self, table: str, ids: list[str]) -> set[str]:
        found: set[str] = set()
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = "SELECT match_id FROM %s WHERE match_id IN (%s)" % (
                    table, ",".join("?" * len(chunk)))
                found.update(r[0] for r in self._db.execute(q, chunk))
        return found

    def missing_matches(self, ids: Iterable[str]) -> set[str]:
        ids = list(ids)
        return set(ids) - self._present("matches", ids)

    def missing_timelines(self, ids: Iterable[str]) -> set[str]:
        """IDs with no timeline row at all (a NULL row counts as known)."""
        ids = list(ids)
        return set(ids) - self._present("timelines", ids)

    def timelines_unavailable(self, ids: Iterable[str]) -> set[str]:
        """IDs whose timeline is absent or known-missing (no decompression)."""
        ids = list(ids)
        found: set[str] = set()
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ("SELECT match_id FROM timelines WHERE data IS NOT NULL "
                     "AND match_id IN (%s)" % ",".join("?" * len(chunk)))
                found.update(r[0] for r in self._db.execute(q, chunk))
        return set(ids) - found

    def player_match_ids(self, puuid: str, queues: Iterable[int] | None = None,
                         limit: int | None = None) -> list[str]:
        """The player's cached matches, newest first."""
        sql = ("SELECT m.match_id FROM participants p JOIN matches m "
               "ON m.match_id = p.match_id WHERE p.puuid = ?")
        args: list = [puuid]
        if queues:
            qs = list(queues)
            sql += " AND m.queue_id IN (%s)" % ",".join("?" * len(qs))
            args += qs
        sql += " ORDER BY m.game_creation DESC"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._lock:
            return [r[0] for r in self._db.execute(sql, args)]

    # -- players --------------------------------------------------------
    def upsert_player(self, puuid: str, game_name: str, tag_line: str,
                      region: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO players VALUES (?,?,?,?,?) ON CONFLICT(puuid) DO "
                "UPDATE SET game_name=excluded.game_name, "
                "tag_line=excluded.tag_line, region=excluded.region, "
                "last_synced=excluded.last_synced",
                (puuid, game_name, tag_line, region, int(time.time())))

    def find_player(self, game_name: str, tag_line: str,
                    region: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT puuid FROM players WHERE lower(game_name)=lower(?) "
                "AND lower(tag_line)=lower(?) AND region=?",
                (game_name, tag_line, region)).fetchone()
        return row[0] if row else None

    # -- rank snapshots -------------------------------------------------
    def add_rank_snapshot(self, puuid: str, entry: dict,
                          taken_at: int | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO rank_snapshots (puuid, taken_at, queue_type, "
                "tier, division, lp, wins, losses) VALUES (?,?,?,?,?,?,?,?)",
                (puuid, int(taken_at or time.time()), entry.get("queueType"),
                 entry.get("tier"), entry.get("rank"),
                 entry.get("leaguePoints"), entry.get("wins"),
                 entry.get("losses")))

    def rank_history(self, puuid: str,
                     queue_type: str = "RANKED_SOLO_5x5") -> list[RankSnapshot]:
        with self._lock:
            rows = self._db.execute(
                "SELECT taken_at, queue_type, tier, division, lp, wins, losses "
                "FROM rank_snapshots WHERE puuid=? AND queue_type=? "
                "ORDER BY taken_at", (puuid, queue_type)).fetchall()
        return [RankSnapshot(*r) for r in rows]

    # -- benchmarks (games of higher-ranked players) ----------------------
    def add_benchmark(self, region: str, level: str,
                      match_ids: Iterable[str]) -> None:
        now = int(time.time())
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO benchmark_games VALUES (?,?,?,?)",
                [(region, level, m, now) for m in match_ids])

    def benchmark_ids(self, region: str, level: str) -> list[str]:
        """Benchmark games whose match JSON is actually cached."""
        with self._lock:
            rows = self._db.execute(
                "SELECT b.match_id FROM benchmark_games b JOIN matches m "
                "ON m.match_id = b.match_id WHERE b.region=? AND b.level=? "
                "ORDER BY b.match_id", (region, level)).fetchall()
        return [r[0] for r in rows]

    def benchmark_info(self, region: str, level: str) -> tuple[int, int]:
        """(games cached, newest added_at epoch s or 0)."""
        with self._lock:
            n, t = self._db.execute(
                "SELECT COUNT(*), MAX(b.added_at) FROM benchmark_games b "
                "JOIN matches m ON m.match_id = b.match_id "
                "WHERE b.region=? AND b.level=?", (region, level)).fetchone()
        return int(n or 0), int(t or 0)

    # -- derived data (opaque to the cache) ------------------------------
    def get_features(self, ids: list[str], version: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ("SELECT match_id, data FROM features WHERE version=? AND "
                     "match_id IN (%s)" % ",".join("?" * len(chunk)))
                for mid, blob in self._db.execute(q, [version] + chunk):
                    out[mid] = _unpack(blob)
        return out

    def put_features(self, rows: list[tuple[str, dict]], version: str) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO features VALUES (?,?,?)",
                [(mid, version, _pack(d)) for mid, d in rows])

    def get_blob(self, key: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM analysis_cache WHERE "
                                   "key=?", (key,)).fetchone()
        return row[0] if row else None

    def put_blob(self, key: str, data: bytes) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO analysis_cache VALUES "
                             "(?,?,?)", (key, int(time.time()), data))

    # -- housekeeping ---------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            q = lambda s: self._db.execute(s).fetchone()[0]
            return {
                "matches": q("SELECT COUNT(*) FROM matches"),
                "timelines": q("SELECT COUNT(*) FROM timelines "
                               "WHERE data IS NOT NULL"),
                "players": q("SELECT COUNT(*) FROM players"),
                "rank_snapshots": q("SELECT COUNT(*) FROM rank_snapshots"),
            }
