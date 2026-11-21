"""The two local APIs on your own PC (no Riot key needed).

* **League client (LCU)** — https://127.0.0.1:<port>, password from the
  client's ``lockfile``; it changes every time the client starts. Used to
  check, download and start replays.
* **Replay API** — https://127.0.0.1:2999 while a replay is open, when
  ``EnableReplayApi=1`` is set in ``Config/game.cfg``. Used to read/seek the
  replay clock, pause, change speed and lock the camera on you.

Both use a self-signed certificate. Certificate checks are skipped for
these two addresses only (127.0.0.1); nothing here talks to the internet.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_LOCAL = ssl.create_default_context()
_LOCAL.check_hostname = False
_LOCAL.verify_mode = ssl.CERT_NONE
# never through a system/corporate proxy: these are our own 127.0.0.1 ports
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      urllib.request.HTTPSHandler(
                                          context=_LOCAL))

DEFAULT_INSTALL = Path(r"C:\Riot Games\League of Legends")
REPLAY_PORT = 2999
COMPONENT = {"componentType": "replay-button_match-history"}


class LocalError(Exception):
    def __init__(self, msg, status: int | None = None):
        super().__init__(msg)
        self.status = status


class NotRunning(LocalError):
    pass


def _request(url: str, method: str = "GET", body=None, auth: str | None = None,
             timeout: float = 3.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(
            ("riot:%s" % auth).encode()).decode())
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise LocalError("%s %s -> HTTP %d" % (method, url, e.code),
                         e.code) from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise NotRunning(str(e)) from e
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")


# ------------------------------------------------------------------- setup

def install_dir(pref: str | None = None) -> Path:
    for p in (pref, os.environ.get("REPLAY_LEAGUE_DIR"), str(DEFAULT_INSTALL)):
        if p and Path(p).exists():
            return Path(p)
    return Path(pref) if pref else DEFAULT_INSTALL


def game_cfg(install: Path) -> Path:
    return install / "Config" / "game.cfg"


def replay_api_enabled(install: Path) -> bool | None:
    """True / False from game.cfg, None if the file can't be read."""
    try:
        text = game_cfg(install).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^EnableReplayApi\s*=\s*(\d)", text, re.M)
    return bool(m and m.group(1) == "1")


def enable_replay_api(install: Path) -> bool:
    """Set EnableReplayApi=1 in game.cfg (under [General]); keeps a backup
    the first time. Returns True if the file now has it on."""
    p = game_cfg(install)
    try:
        raw = p.read_bytes()
    except OSError:
        return False
    text = raw.decode("latin-1")          # byte-exact round trip
    nl = "\r\n" if "\r\n" in text else "\n"
    if re.search(r"^EnableReplayApi\s*=\s*1", text, re.M):
        return True
    bak = p.with_name("game.cfg.bak-before-replayapi")
    if not bak.exists():
        try:
            bak.write_bytes(raw)
        except OSError:
            pass
    lines = text.split(nl)
    key = re.compile(r"^EnableReplayApi\s*=")
    hit = next((i for i, ln in enumerate(lines) if key.match(ln)), None)
    if hit is not None:
        lines[hit] = "EnableReplayApi=1"
    else:
        gen = next((i for i, ln in enumerate(lines)
                    if ln.strip().lower() == "[general]"), None)
        if gen is not None:
            lines.insert(gen + 1, "EnableReplayApi=1")
        else:
            lines[:0] = ["[General]", "EnableReplayApi=1"]
    text = nl.join(lines)
    try:
        p.write_bytes(text.encode("latin-1"))
    except OSError:
        return False
    return True


# --------------------------------------------------------------------- LCU

@dataclass
class Lockfile:
    pid: int
    port: int
    password: str
    protocol: str


def read_lockfile(install: Path) -> Lockfile | None:
    try:
        parts = (install / "lockfile").read_text().strip().split(":")
        return Lockfile(int(parts[1]), int(parts[2]), parts[3], parts[4])
    except (OSError, IndexError, ValueError):
        return None


# what the client says about a replay -> what we show
REPLAY_STATE = {
    "watch": "Ready to watch",
    "download": "Not downloaded",
    "downloading": "Downloading…",
    "checking": "Checking…",
    "incompatible": "Old patch — can't play",
    "missingOrExpired": "No longer available",
    "lost": "Not available",
    "unsupported": "Not supported",
    "retryDownload": "Download failed — retry",
    "found": "Checking…",
    "maxLimitReached": "Replay folder full",
    "error": "Client error",
}


class LeagueClient:
    def __init__(self, install: Path):
        self.install = install

    def _lf(self) -> Lockfile:
        lf = read_lockfile(self.install)
        if lf is None:
            raise NotRunning("League client is not running")
        return lf

    def call(self, path: str, method="GET", body=None, timeout=4.0):
        lf = self._lf()
        return _request("https://127.0.0.1:%d%s" % (lf.port, path), method,
                        body, lf.password, timeout)

    def running(self) -> bool:
        try:
            self.call("/lol-replays/v1/configuration", timeout=1.5)
            return True
        except LocalError:
            return False

    def configuration(self) -> dict:
        return self.call("/lol-replays/v1/configuration") or {}

    def metadata(self, game_id: int) -> dict:
        """{'state': 'watch'|'download'|…, 'downloadProgress': 0–100}, or {}
        for a game the client hasn't loaded yet (it answers 404 until it has
        seen the game in match history, or we ``create_metadata`` it).
        ``downloadProgress`` is only meaningful while downloading (checked
        on 16.23: other states carry junk like 2349731080)."""
        try:
            md = self.call("/lol-replays/v1/metadata/%d" % game_id) or {}
        except LocalError as e:
            if e.status == 404:
                return {}
            raise
        if md.get("state") != "downloading":
            md.pop("downloadProgress", None)
        elif isinstance(md.get("downloadProgress"), int):
            md["downloadProgress"] = max(0, min(md["downloadProgress"], 100))
        return md

    def create_metadata(self, game_id: int, match: dict) -> None:
        """Tell the client about a game it hasn't seen (opened outside its
        match history) so it can check/download the replay."""
        info = match.get("info", {})
        # LolReplaysReplayCreateMetadata on 16.23: exactly these four
        body = {"gameEnd": info.get("gameEndTimestamp", 0),
                "gameType": info.get("gameType", "MATCHED_GAME"),
                "gameVersion": info.get("gameVersion", ""),
                "queueId": info.get("queueId", 420)}
        self.call("/lol-replays/v2/metadata/%d/create" % game_id, "POST", body)

    def download(self, game_id: int) -> None:
        self.call("/lol-replays/v1/rofls/%d/download/graceful" % game_id,
                  "POST", COMPONENT)

    def watch(self, game_id: int) -> None:
        self.call("/lol-replays/v1/rofls/%d/watch" % game_id, "POST",
                  COMPONENT)

    def replays_folder(self) -> str | None:
        try:
            p = self.call("/lol-replays/v1/rofls/path")
            return p if isinstance(p, str) else None
        except LocalError:
            return None


# -------------------------------------------------------------- Replay API

class ReplayAPI:
    def __init__(self, port: int = REPLAY_PORT):
        self.base = "https://127.0.0.1:%d" % port

    def _call(self, path, method="GET", body=None, timeout=1.5):
        return _request(self.base + path, method, body, None, timeout)

    def playback(self) -> dict:
        """{'time': s, 'length': s, 'paused': bool, 'speed': x, …}"""
        return self._call("/replay/playback") or {}

    def set_playback(self, time: float | None = None,
                     paused: bool | None = None,
                     speed: float | None = None) -> dict:
        body = {}
        if time is not None:
            body["time"] = max(float(time), 0.0)
        if paused is not None:
            body["paused"] = bool(paused)
        if speed is not None:
            body["speed"] = float(speed)
        return self._call("/replay/playback", "POST", body) or {}

    def render(self) -> dict:
        return self._call("/replay/render") or {}

    def set_render(self, **fields) -> dict:
        return self._call("/replay/render", "POST", fields) or {}

    def players(self) -> list[dict]:
        """Live Client Data player list (also served during replays)."""
        try:
            r = self._call("/liveclientdata/playerlist")
            return r if isinstance(r, list) else []
        except LocalError:
            return []

    def follow(self, champion: str, riot_name: str = "") -> str:
        """Keep the camera on you. Returns 'locked' (on your champion),
        'attached' (camera follows whatever is selected in the replay —
        click your champion once) or 'failed'.

        Checked against the Replay API's OpenAPI spec on 16.23:
        ``Render.selectionName`` "sets the selection to the given name, case
        insensitive". The game takes the champion name in either form
        ("MissFortune" from Riot's API or "Miss Fortune") and reads back the
        display form; Riot IDs are rejected (reads back ""). So: champion
        names first, compared without spaces/punctuation."""
        try:
            r = self.render()
        except LocalError:
            return "failed"
        if "selectionName" in r:
            want = {_norm(champion)}
            names = [champion]
            for p in self.players():
                raw = (p.get("rawChampionName") or "").rsplit("_", 1)[-1]
                if _norm(p.get("championName")) in want or _norm(raw) in want                         or (riot_name and _norm(p.get("riotIdGameName"))
                            == _norm(riot_name)):
                    want.add(_norm(p.get("championName")))
                    names.insert(0, p.get("championName"))
                    names += [p.get("riotIdGameName"), p.get("riotId")]
            names.append(riot_name)
            seen = set()
            for n in names:
                if not n or _norm(n) in seen:
                    continue
                seen.add(_norm(n))
                try:
                    self.set_render(selectionName=n, cameraAttached=True)
                    got = _norm(self.render().get("selectionName"))
                except LocalError:
                    return "failed"
                if got and (got == _norm(n) or got in want):
                    return "locked"
        try:
            self.set_render(cameraAttached=True)
        except LocalError:
            return "failed"
        return "attached"


    # ---- keeping the camera on you (checked live on 16.23) --------------
    # Every seek (a jump, ±10 s) clears the selection when it finishes, so
    # a lock sent with the seek is lost: wait for the seek, then lock.
    # selectionName also reads back "" a second after a lock while the
    # camera still follows, so "is it still on me?" is answered from where
    # the champion is on screen instead.

    def wait_seek(self, timeout: float = 4.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.playback().get("seeking"):
                return
            time.sleep(0.05)

    def find_player(self, champion: str, riot_name: str = "") -> dict | None:
        want = _norm(champion)
        for p in self.players():
            raw = (p.get("rawChampionName") or "").rsplit("_", 1)[-1]
            if _norm(p.get("championName")) == want or _norm(raw) == want                     or (riot_name and _norm(p.get("riotIdGameName"))
                        == _norm(riot_name)):
                return p
        return None

    def on_screen(self, champion: str, riot_name: str = "") -> bool | None:
        """True: you're on screen. False: alive but off screen (the camera
        went elsewhere). None: dead, not in this replay, or unknown."""
        p = self.find_player(champion, riot_name)
        if p is None or p.get("isDead"):
            return None
        return _visible(p.get("screenPositionCenter"))

    def follow_after_seek(self, champion: str, riot_name: str = "",
                          settle: float = 8.0, step: float = 0.5) -> str:
        """Lock once the seek has finished. For a few seconds after a seek
        the game keeps moving the camera (live: off screen for up to ~6 s
        after a "locked" answer), so keep checking every ``step`` s for up
        to ``settle`` s and lock again until you're on screen twice in a
        row. Dead or not found: one lock and done."""
        self.wait_seek()
        r = self.follow(champion, riot_name)
        if r != "locked":
            return r
        end, good = time.monotonic() + settle, 0
        while time.monotonic() < end and good < 2:
            time.sleep(step)
            vis = self.on_screen(champion, riot_name)
            if vis is None:
                break
            if vis:
                good += 1
            else:
                good = 0
                self.follow(champion, riot_name)
        return r

def _norm(name) -> str:
    """'Miss Fortune' / 'MissFortune' / "Kai'Sa" -> 'missfortune' / 'kaisa'."""
    return re.sub(r"[^0-9a-z]", "", (name or "").lower())


def _visible(pos) -> bool:
    """'632.15,728.67' -> True. Off screen the game reports 3.4e38 for both
    (FLT_MAX); single glitch frames of that happen while following too, so
    callers should want two in a row before acting."""
    try:
        x, y = (float(v) for v in str(pos).split(","))
    except ValueError:
        return False
    return abs(x) < 1e6 and abs(y) < 1e6
