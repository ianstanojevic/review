"""Record highlight clips with League's own replay recorder.

Flow (all on a worker thread, cancellable between and inside clips):

1. ``open_replay``: the same path as REPLAY's Watch — ask the client about
   the game (``create`` it if the client hasn't seen it), download if
   needed, ``watch``, then wait for the Replay API (it answers 404 for
   ~20 s while the game loads).
2. per clip: seek to a little before the clip → ``follow_after_seek``
   (camera on you; it pans for a few seconds) → start the recorder with
   the clip's start/end times → wait (real time) → stop → move the file
   into place. No seeks or camera locks while a clip is recording.

The Replay API's ``/replay/recording`` (Riot's ``Recording`` schema):
``recording`` on/off, ``startTime``/``endTime`` in replay seconds,
``codec`` (webm), ``framesPerSecond``, ``width``, ``height``, ``lossless``,
``enforceFrameRate``, ``replaySpeed`` and ``path``; GET also reports
``currentTime``. Checked live: see docs/HIGHLIGHTS.md.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from replay import local

PREROLL_S = 7.0           # seek this far before the clip: camera pans to you
STOP_GRACE_S = 6.0        # past endTime before we stop it ourselves
LOAD_TIMEOUT_S = 240.0    # download + game start
CANT_RECORD = {"incompatible", "missingOrExpired", "lost", "unsupported",
               "error", "maxLimitReached"}

RESOLUTIONS = [((1280, 720), "720p"), ((1920, 1080), "1080p"),
               ((2560, 1440), "1440p")]
FPS = [(30, "30 fps"), (60, "60 fps")]


class Cancelled(Exception):
    pass


class NotRecordable(Exception):
    """This game's replay can't be played (old patch, expired, …)."""


@dataclass
class Clip:
    start_s: float
    end_s: float
    target: Path
    title: str = ""


@dataclass
class ClipResult:
    clip: Clip
    ok: bool
    path: Path | None = None
    error: str = ""


@dataclass
class Progress:
    phase: str            # loading | seeking | camera | recording | saving |
    #                       done | error | cancelled
    index: int = 0        # clip number (0-based)
    total: int = 0
    frac: float = 0.0     # of the current clip's recording
    message: str = ""


def videos_dir() -> Path:
    """The user's Videos folder (also when it's moved, e.g. to OneDrive)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                            ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
            u = UUID("{18989B1D-99B5-455B-841C-AB7C74E4DDFC}")   # Videos
            g = GUID(u.fields[0], u.fields[1], u.fields[2],
                     (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                out = Path(p.value)
                ctypes.windll.ole32.CoTaskMemFree(p)
                return out
        except Exception:
            pass
    return Path.home() / "Videos"


def default_out_dir() -> Path:
    return videos_dir() / "Highlights"


# ------------------------------------------------------------ open replay

def open_replay(lcu: local.LeagueClient, rapi: local.ReplayAPI,
                game_id: int, match: dict, champions: set[str],
                cancel: threading.Event, status=lambda msg: None,
                timeout: float = LOAD_TIMEOUT_S, poll: float = 1.0) -> None:
    """Make sure the replay of ``game_id`` is open in the game. Returns
    when the Replay API answers for it; raises ``NotRecordable``,
    ``local.NotRunning`` (no client) or ``Cancelled``."""
    if _replay_is(rapi, champions):
        return
    md = lcu.metadata(game_id)
    if not md:
        lcu.create_metadata(game_id, match)
        md = lcu.metadata(game_id)
    end = time.monotonic() + timeout
    asked_download = False
    while True:
        state = md.get("state")
        if state in CANT_RECORD:
            raise NotRecordable(local.REPLAY_STATE.get(state, state))
        if state == "watch":
            break
        if state in ("download", "retryDownload") and not asked_download:
            status("Downloading the replay…")
            lcu.download(game_id)
            asked_download = True
        elif state == "downloading" and md.get("downloadProgress"):
            status("Downloading the replay… %d%%" % md["downloadProgress"])
        _wait(cancel, poll, end, "The replay didn't download in time")
        md = lcu.metadata(game_id)
    if local.replay_api_enabled(lcu.install) is False:
        local.enable_replay_api(lcu.install)
    status("Starting the replay…")
    lcu.watch(game_id)
    while True:       # the Replay API answers 404 while the game loads
        _wait(cancel, poll, end, "The replay didn't open in time")
        try:
            pb = rapi.playback()
        except local.LocalError:
            continue
        if pb.get("length"):
            break
    status("Replay open")


def _replay_is(rapi: local.ReplayAPI, champions: set[str]) -> bool:
    """Is a replay open, and is it this game (same ten champions)?"""
    try:
        if not rapi.playback().get("length"):
            return False
    except local.LocalError:
        return False
    names = set()
    for p in rapi.players():
        raw = (p.get("rawChampionName") or "").rsplit("_", 1)[-1]
        names.add(local._norm(p.get("championName")))
        names.add(local._norm(raw))
    want = {local._norm(c) for c in champions}
    return bool(want) and want <= names


def _wait(cancel: threading.Event, secs: float, deadline: float | None = None,
          timeout_msg: str = "") -> None:
    if cancel.wait(secs):
        raise Cancelled()
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError(timeout_msg)


# ---------------------------------------------------------------- record

class Recorder:
    """Records clips one after another. ``on_progress(Progress)`` is called
    from the worker thread."""

    def __init__(self, rapi: local.ReplayAPI, champion: str, riot_name: str,
                 width: int = 1920, height: int = 1080, fps: int = 60,
                 on_progress=lambda p: None,
                 cancel: threading.Event | None = None,
                 poll: float = 0.25):
        self.rapi = rapi
        self.me = (champion, riot_name)
        self.width, self.height, self.fps = width, height, fps
        self.on_progress = on_progress
        self.cancel = cancel or threading.Event()
        self.poll = poll

    def record_all(self, clips: list[Clip]) -> list[ClipResult]:
        out: list[ClipResult] = []
        n = len(clips)
        try:
            for i, c in enumerate(clips):
                try:
                    path = self.record(c, i, n)
                    out.append(ClipResult(c, True, path))
                except (local.LocalError, OSError, TimeoutError) as e:
                    out.append(ClipResult(c, False, error=str(e)))
                    self.on_progress(Progress("error", i, n, 0, str(e)))
        except Cancelled:
            self._stop_quietly()
            self.on_progress(Progress("cancelled", len(out), n, 0,
                                      "Cancelled"))
            return out
        try:
            self.rapi.set_playback(paused=True)
        except local.LocalError:
            pass
        self.on_progress(Progress("done", n, n, 1.0, ""))
        return out

    def record(self, c: Clip, i: int = 0, n: int = 1) -> Path:
        rapi, me = self.rapi, self.me
        prog = self.on_progress
        if self._recording():
            self._stop_quietly()
        prog(Progress("seeking", i, n, 0, c.title))
        rapi.set_playback(time=max(c.start_s - PREROLL_S, 0), paused=False,
                          speed=1.0)
        prog(Progress("camera", i, n, 0, c.title))
        rapi.follow_after_seek(*me)
        _wait(self.cancel, 0)
        c.target.parent.mkdir(parents=True, exist_ok=True)
        if c.target.exists():
            c.target.unlink()
        started = time.time()
        # the game starts and stops on its own at start/endTime; no seeks or
        # camera calls from here until it's done
        rapi._call("/replay/recording", "POST", {
            "recording": True, "startTime": float(c.start_s),
            "endTime": float(c.end_s), "codec": "webm",
            "framesPerSecond": int(self.fps), "width": int(self.width),
            "height": int(self.height), "enforceFrameRate": True,
            "lossless": False, "replaySpeed": 1.0, "path": str(c.target)})
        length = max(c.end_s - c.start_s, 0.1)
        while True:
            _wait(self.cancel, self.poll)
            st = self._state()
            now = st.get("currentTime")
            if now is None:
                now = rapi.playback().get("time", 0.0)
            prog(Progress("recording", i, n, max(0.0, min(
                (now - c.start_s) / length, 1.0)), c.title))
            if not st.get("recording") and now >= c.end_s - 0.5:
                break
            if now >= c.end_s + STOP_GRACE_S:
                self._stop_quietly()
                break
        prog(Progress("saving", i, n, 1.0, c.title))
        return self._collect(c, st, started)

    # ---- helpers
    def _state(self) -> dict:
        r = self.rapi._call("/replay/recording")
        return r if isinstance(r, dict) else {}

    def _recording(self) -> bool:
        try:
            return bool(self._state().get("recording"))
        except local.LocalError:
            return False

    def _stop_quietly(self):
        try:
            self.rapi._call("/replay/recording", "POST", {"recording": False})
        except local.LocalError:
            pass

    def _collect(self, c: Clip, st: dict, started: float) -> Path:
        """The finished file: at ``c.target`` if the game wrote it there,
        else the newest video the game wrote since the clip started (moved
        into place). Waits a few seconds for the encoder to finish."""
        end = time.monotonic() + 15
        while True:
            if _done_file(c.target):
                return c.target
            found = _newest_video(_candidates(st.get("path"), c.target),
                                  started)
            if found is not None and _done_file(found):
                shutil.move(str(found), str(c.target))
                return c.target
            if time.monotonic() > end:
                raise OSError("The game didn't write a video for %s"
                              % c.target.name)
            _wait(self.cancel, 0.5)


def _done_file(p: Path) -> bool:
    """Exists, non-empty and not growing any more."""
    try:
        a = p.stat().st_size
        if not a:
            return False
        time.sleep(0.3)
        return p.stat().st_size == a
    except OSError:
        return False


def _candidates(reported, target: Path) -> list[Path]:
    out = []
    if reported:
        r = Path(str(reported))
        out.append(r if r.is_dir() else r.parent)
    home = Path.home()
    out += [target.parent, videos_dir() / "League of Legends",
            home / "Documents" / "League of Legends" / "Highlights",
            videos_dir()]
    seen, uniq = set(), []
    for d in out:
        k = os.path.normcase(str(d))
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


def _newest_video(dirs: list[Path], since: float) -> Path | None:
    best = None
    for d in dirs:
        try:
            for f in d.iterdir():
                if f.suffix.lower() not in (".webm", ".mp4"):
                    continue
                m = f.stat().st_mtime
                if m >= since - 2 and (best is None or m > best[0]):
                    best = (m, f)
        except OSError:
            continue
    return best[1] if best else None
