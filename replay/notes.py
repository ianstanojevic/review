"""Your annotations: tagged deaths, missed chances, bookmarks and matchup
notes.

Kept in their own SQLite file (``replay.sqlite`` beside Review's cache),
not in ``review.sqlite``: the cache can be cleared and rebuilt from Riot at
any time, your notes can't. Review and Draft open this file read-only to
show the totals / your notes for a matchup.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .model import BOOKMARK, DEATH, MISSED

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS marks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id  TEXT NOT NULL,
    puuid     TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- death | missed | bookmark
    t_ms      INTEGER NOT NULL,       -- game time (timeline clock)
    category  TEXT,
    avoidable INTEGER,                -- 1 / 0 / NULL
    note      TEXT NOT NULL DEFAULT '',
    champion  TEXT,                   -- yours, for per-champion totals
    created   INTEGER,
    updated   INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_marks_death
    ON marks(match_id, puuid, t_ms) WHERE kind = 'death';
CREATE INDEX IF NOT EXISTS ix_marks_player ON marks(puuid, kind);
CREATE TABLE IF NOT EXISTS matchups (
    match_id     TEXT NOT NULL,
    puuid        TEXT NOT NULL,
    champion     TEXT,
    opponent     TEXT,
    ally_support TEXT,
    enemy_support TEXT,
    lane         INTEGER,             -- 1 lost hard … 5 won hard
    note         TEXT NOT NULL DEFAULT '',
    reviewed     INTEGER NOT NULL DEFAULT 0,
    updated      INTEGER,
    PRIMARY KEY (match_id, puuid)
);
CREATE INDEX IF NOT EXISTS ix_matchups_pair ON matchups(champion, opponent);
"""


@dataclass
class Mark:
    id: int
    match_id: str
    puuid: str
    kind: str
    t_ms: int
    category: str | None = None
    avoidable: int | None = None
    note: str = ""
    champion: str | None = None

    @property
    def tagged(self) -> bool:
        return self.kind == BOOKMARK or bool(self.category)


@dataclass
class Matchup:
    match_id: str
    puuid: str
    champion: str | None = None
    opponent: str | None = None
    ally_support: str | None = None
    enemy_support: str | None = None
    lane: int | None = None
    note: str = ""
    reviewed: bool = False
    updated: int | None = None


_MARK_COLS = ("id, match_id, puuid, kind, t_ms, category, avoidable, note, "
              "champion")


class Notes:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.execute("INSERT OR IGNORE INTO meta VALUES "
                             "('schema_version', ?)", (str(SCHEMA_VERSION),))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- marks ----------------------------------------------------------
    def marks(self, match_id: str, puuid: str) -> list[Mark]:
        with self._lock:
            rows = self._db.execute(
                "SELECT %s FROM marks WHERE match_id=? AND puuid=? "
                "ORDER BY t_ms" % _MARK_COLS, (match_id, puuid)).fetchall()
        return [Mark(*r) for r in rows]

    def ensure_deaths(self, match_id: str, puuid: str, times_ms: list[int],
                      champion: str | None = None) -> None:
        """One (untagged) death mark per death in the timeline."""
        now = int(time.time())
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO marks (match_id, puuid, kind, t_ms, "
                "champion, created, updated) VALUES (?,?,?,?,?,?,?)",
                [(match_id, puuid, DEATH, int(t), champion, now, now)
                 for t in times_ms])

    def add_mark(self, match_id: str, puuid: str, kind: str, t_ms: int,
                 champion: str | None = None, category: str | None = None,
                 note: str = "") -> Mark:
        if kind not in (MISSED, BOOKMARK):
            raise ValueError("deaths come from the timeline")
        now = int(time.time())
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO marks (match_id, puuid, kind, t_ms, category, "
                "note, champion, created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                (match_id, puuid, kind, int(t_ms), category, note or "",
                 champion, now, now))
            mid = cur.lastrowid
        return self.get_mark(mid)

    def get_mark(self, mark_id: int) -> Mark | None:
        with self._lock:
            r = self._db.execute("SELECT %s FROM marks WHERE id=?"
                                 % _MARK_COLS, (mark_id,)).fetchone()
        return Mark(*r) if r else None

    def update_mark(self, mark_id: int, **fields) -> None:
        allowed = {"category", "avoidable", "note", "t_ms"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sets["updated"] = int(time.time())
        with self._lock, self._db:
            self._db.execute(
                "UPDATE marks SET %s WHERE id=?" % ", ".join(
                    "%s=?" % k for k in sets),
                (*sets.values(), mark_id))

    def delete_mark(self, mark_id: int) -> None:
        """Your own marks only: a death can't be deleted, only untagged."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM marks WHERE id=? AND kind != ?",
                             (mark_id, DEATH))

    # -- matchups -------------------------------------------------------
    def matchup(self, match_id: str, puuid: str) -> Matchup | None:
        with self._lock:
            r = self._db.execute(
                "SELECT match_id, puuid, champion, opponent, ally_support, "
                "enemy_support, lane, note, reviewed, updated FROM matchups "
                "WHERE match_id=? AND puuid=?", (match_id, puuid)).fetchone()
        if not r:
            return None
        m = Matchup(*r)
        m.reviewed = bool(m.reviewed)
        return m

    def save_matchup(self, m: Matchup) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO matchups VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(match_id, puuid) DO UPDATE SET "
                "champion=excluded.champion, opponent=excluded.opponent, "
                "ally_support=excluded.ally_support, "
                "enemy_support=excluded.enemy_support, lane=excluded.lane, "
                "note=excluded.note, reviewed=excluded.reviewed, "
                "updated=excluded.updated",
                (m.match_id, m.puuid, m.champion, m.opponent, m.ally_support,
                 m.enemy_support, m.lane, m.note or "", int(m.reviewed),
                 int(time.time())))

    def matchup_history(self, champion: str | None, opponent: str | None,
                        exclude_match: str | None = None,
                        puuids: list[str] | None = None) -> list[Matchup]:
        """Earlier notes against the same opponent (any of your champs
        if ``champion`` is None), newest first. Only rows with a note or a
        lane rating."""
        sql = ("SELECT match_id, puuid, champion, opponent, ally_support, "
               "enemy_support, lane, note, reviewed, updated FROM matchups "
               "WHERE opponent=? AND (note != '' OR lane IS NOT NULL)")
        args: list = [opponent]
        if champion:
            sql += " AND champion=?"
            args.append(champion)
        if exclude_match:
            sql += " AND match_id != ?"
            args.append(exclude_match)
        if puuids:
            sql += " AND puuid IN (%s)" % ",".join("?" * len(puuids))
            args += puuids
        sql += " ORDER BY updated DESC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            m = Matchup(*r)
            m.reviewed = bool(m.reviewed)
            out.append(m)
        return out

    def all_matchups(self, puuid: str) -> list[Matchup]:
        with self._lock:
            rows = self._db.execute(
                "SELECT match_id, puuid, champion, opponent, ally_support, "
                "enemy_support, lane, note, reviewed, updated FROM matchups "
                "WHERE puuid=? AND (note != '' OR lane IS NOT NULL) "
                "ORDER BY updated DESC", (puuid,)).fetchall()
        return [Matchup(*r[:8], bool(r[8]), r[9]) for r in rows]

    # -- totals ---------------------------------------------------------
    def progress(self, puuid: str) -> dict[str, tuple[int, int]]:
        """match_id -> (tagged moments, moments) for the games list."""
        with self._lock:
            rows = self._db.execute(
                "SELECT match_id, SUM(CASE WHEN category IS NOT NULL OR "
                "kind=? THEN 1 ELSE 0 END), COUNT(*) FROM marks WHERE "
                "puuid=? GROUP BY match_id", (BOOKMARK, puuid)).fetchall()
        return {r[0]: (int(r[1] or 0), int(r[2])) for r in rows}

    def reviewed_games(self, puuid: str) -> set[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT match_id FROM matchups WHERE puuid=? AND reviewed=1",
                (puuid,)).fetchall()
        return {r[0] for r in rows}

    def category_counts(self, puuid: str, kind: str,
                        champion: str | None = None,
                        match_ids: list[str] | None = None
                        ) -> dict[str, tuple[int, int]]:
        """category -> (count, of which avoidable=yes)."""
        sql = ("SELECT category, COUNT(*), SUM(CASE WHEN avoidable=1 THEN 1 "
               "ELSE 0 END) FROM marks WHERE puuid=? AND kind=? AND "
               "category IS NOT NULL")
        args: list = [puuid, kind]
        if champion:
            sql += " AND champion=?"
            args.append(champion)
        if match_ids is not None:
            if not match_ids:
                return {}
            sql += " AND match_id IN (%s)" % ",".join("?" * len(match_ids))
            args += match_ids
        sql += " GROUP BY category"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return {r[0]: (int(r[1]), int(r[2] or 0)) for r in rows}

    def tagged_counts(self, puuid: str) -> tuple[int, int, int]:
        """(tagged deaths, all death marks, games with any tag)."""
        with self._lock:
            r = self._db.execute(
                "SELECT SUM(CASE WHEN category IS NOT NULL THEN 1 ELSE 0 END),"
                " COUNT(*), COUNT(DISTINCT CASE WHEN category IS NOT NULL "
                "THEN match_id END) FROM marks WHERE puuid=? AND kind=?",
                (puuid, DEATH)).fetchone()
        return int(r[0] or 0), int(r[1] or 0), int(r[2] or 0)

    def recent_marks(self, puuid: str, kind: str, limit: int = 200
                     ) -> list[Mark]:
        with self._lock:
            rows = self._db.execute(
                "SELECT %s FROM marks WHERE puuid=? AND kind=? AND "
                "(category IS NOT NULL OR note != '') ORDER BY updated DESC "
                "LIMIT ?" % _MARK_COLS, (puuid, kind, limit)).fetchall()
        return [Mark(*r) for r in rows]


def notes_path():
    from review import config
    return config.data_dir() / "replay.sqlite"
