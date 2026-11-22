"""HIGHLIGHTS: scoring and merging (pure logic), the recording flow against
a fake Replay API + League client, and the UI on ONE shared Tk root (a
second Tk() in the same process is flaky on Windows/Python 3.14)."""
import functools
import gzip
import http.server
import json
import re
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

from highlights import recorder, score
from highlights.recorder import Clip, Recorder
from replay import local
from replay.events import GameView, Moment, Player, build_game

FIX = Path(__file__).parent / "fixtures"
ME, OPP, ESUP, EJG = 4, 9, 10, 7


# ============================================================== scoring
def _game(moments, role="BOTTOM", duration_s=1800):
    roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    champs = ["Garen", "LeeSin", "Ahri", "Jinx", "Thresh",
              "Darius", "Vi", "Zed", "Draven", "Nautilus"]
    players = {i: Player(i, "p%d" % i, champs[i - 1], roles[(i - 1) % 5],
                         100 if i <= 5 else 200) for i in range(1, 11)}
    me = players[ME]
    me.role = role
    return GameView("EUW1_1", 1, "p4", me, players, True, 0, 0, 0, 0,
                    duration_s, 1763300000000, "16.23.1.1", 420,
                    opponent=players[OPP], ally_support=players[5],
                    enemy_support=players[ESUP], moments=sorted(
                        moments, key=lambda m: m.t_ms))


def kill(t_s, victim=OPP, assists=(), shutdown=0, gold=None):
    return Moment(int(t_s * 1000), "kill", "", detail={
        "killer": ME, "victim": victim, "assists": list(assists),
        "bounty": 300, "shutdown": shutdown, "gold_diff": gold})


def assist(t_s, victim=EJG):
    return Moment(int(t_s * 1000), "assist", "", detail={
        "killer": 3, "victim": victim, "assists": [ME], "bounty": 300,
        "shutdown": 0, "gold_diff": None})


def obj(t_s, name, mine=True, ours=True, last_hit=False, kind="objective"):
    return Moment(int(t_s * 1000), kind, "", ours=ours, detail={
        "killer": ME if last_hit else 2, "assists": [ME] if mine else [],
        "mine": mine, "name": name})


def test_multikill_windows():
    assert score.multikills([0, 5_000, 14_000]) == [(2, 3)]
    assert score.multikills([0, 11_000]) == []
    # quadra -> penta gets 30 s
    assert score.multikills([0, 5_000, 10_000, 15_000, 40_000]) == [(4, 5)]
    assert score.multikills([0, 5_000, 10_000, 15_000, 50_000]) == [(3, 4)]
    # a sixth kill starts over
    assert score.multikills([0, 1e3, 2e3, 3e3, 4e3, 5e3]) == [(4, 5)]
    assert score.multikills([]) == []


def test_kill_points():
    plain = score.beats(_game([kill(100, EJG, assists=[5])]))[0]
    solo_lane = score.beats(_game([kill(100, OPP)]))[0]
    assert plain.points == 10
    assert solo_lane.points == 10 + 8 + 5 + 5
    assert "laner" in solo_lane.label and {"solokill", "lane"} <= \
        solo_lane.tags
    shut = score.beats(_game([kill(100, EJG, [5], shutdown=450,
                                   gold=-2600)]))[0]
    assert shut.points == 10 + (4 + 8) + 10
    assert "450g shutdown" in shut.label and "2.6k behind" in shut.label
    assert score.beats(_game([assist(100)]))[0].points == 3


def test_objectives_only_when_you_took_part():
    g = _game([obj(600, "Baron"), obj(700, "Fire dragon", mine=False),
               obj(800, "Herald", ours=False),
               obj(900, "Mid tower", kind="tower"),
               obj(950, "Elder", last_hit=True)])
    bs = score.beats(g)
    assert [b.label for b in bs] == ["Baron", "Mid tower",
                                     "Elder (your last hit)"]
    assert [b.points for b in bs] == [18, 4, 22 + 4]


def test_teamfight_and_baron_merge_into_one_clip():
    g = _game([kill(600, OPP), kill(605, ESUP), kill(611, EJG),
               obj(622, "Baron"), kill(1200, OPP)])
    hs = score.find_highlights(g)
    assert len(hs) == 2
    h = hs[0]
    assert h.title == "Triple kill + Baron" and h.kind == "triple"
    assert h.start_ms == 600_000 - score.PRE_MS
    assert h.end_ms == 622_000 + score.POST_MS
    assert h.score == sum(b.points for b in h.beats) + score.TEAMFIGHT_BONUS
    assert h.filename == "10m_triple.webm"
    assert hs[1].title == "Solo kill on your laner Draven"
    assert hs[1].kind == "solokill"


def test_long_fights_are_split():
    g = _game([assist(300 + 12 * i) for i in range(20)])
    hs = score.find_highlights(g)
    assert len(hs) > 1
    assert all(h.end_ms - h.start_ms <= score.MAX_CLIP_MS for h in hs)
    assert sum(len(h.beats) for h in hs) == 20


def test_clip_clamped_to_game():
    g = _game([kill(3, OPP), kill(1799, EJG)], duration_s=1800)
    hs = score.find_highlights(g)
    assert hs[0].start_ms == 0 and hs[-1].end_ms == 1_800_000


def test_titles():
    g = _game([kill(100, EJG, [5]), kill(150, OPP, [5]), assist(152)])
    hs = score.find_highlights(g, name_of=lambda c: c.upper())
    assert hs[0].title == "Kill on VI"
    assert hs[1].title == "1 kill, 1 assist"
    g = _game([assist(100), assist(103), assist(106), obj(110, "Mid tower",
                                                          kind="tower")])
    (h,) = score.find_highlights(g)
    assert h.title == "Teamfight: 3 assists + Mid tower"
    assert h.kind == "teamfight"


def test_selection_estimate_and_names():
    g = _game([kill(100, OPP), assist(400), kill(700, OPP), kill(703, EJG),
               assist(1000), obj(1300, "Baron")])
    hs = score.find_highlights(g)
    sel = score.default_selection(hs)
    assert sel and all(hs[i].score >= 15 for i in sel)
    assert not any(hs[i].title.startswith("Assist") for i in sel)
    assert score.default_selection(hs, max_n=1) == {
        max(range(len(hs)), key=lambda i: hs[i].score)}
    weak = score.find_highlights(_game([assist(100)]))
    assert score.default_selection(weak) == {0}       # never nothing
    assert score.default_selection([]) == set()
    picked = [hs[i] for i in sel]
    assert score.estimate_s(picked, replay_open=True) == int(
        sum(h.length_s for h in picked) + score.SETUP_S * len(picked))
    assert score.estimate_s(picked) == score.estimate_s(
        picked, True) + score.LOAD_S
    assert score.estimate_s([]) == 0
    two = [score.Highlight(60_000, 70_000, 5, "a", "kill",
                           [score.Beat(61_000, "kill", 5, "a")])] * 3
    assert score.file_names(two) == ["1m_kill.webm", "1m_kill_2.webm",
                                     "1m_kill_3.webm"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d_Jinx_vs_Draven",
                        score.folder_name(g))
    assert score.folder_for(0, "Kai'Sa", None).endswith("Kai_Sa_vs_unknown")


def test_real_games():
    d = json.load(gzip.open(FIX / "support.json.gz"))
    g = build_game(d["match"], d["timeline"], "puuid-me")
    hs = score.find_highlights(g)
    best = max(hs, key=lambda h: h.score)
    assert best.kind == "double" and best.title == "Double kill"
    assert [h.start_ms for h in hs] == sorted(h.start_ms for h in hs)
    for a, b in zip(hs, hs[1:]):
        assert a.end_ms <= b.start_ms + score.PRE_MS + score.POST_MS
    no_tl = build_game(d["match"], None, "puuid-me")
    assert score.find_highlights(no_tl) == []


# ====================================================== fake game + client
class _Fake(http.server.BaseHTTPRequestHandler):
    """Replay API + LCU in one, behaving like the game did live (16.23).
    Replay time runs SCALE× wall time. Recording: ``startTime`` -1 = now,
    any other value seeks there (and drops the camera lock); it stops by
    itself at ``endTime`` and writes the file (to ``path``, or to
    ``alt_dir`` like a game that ignores the path); with ``hidden`` (a
    full-screen game in the background) it reads back 16×0 and never
    starts."""
    SCALE = 25.0
    s: dict = {}
    log: list = []
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @classmethod
    def reset(cls, **kw):
        cls.s = {"open": True, "t": 0.0, "at": time.monotonic(),
                 "paused": True, "render": {"selectionName": "",
                                            "cameraAttached": False},
                 "rec": {"recording": False}, "state": "watch",
                 "watch_polls": 0, "players": [
                     {"championName": c, "riotIdGameName": "x%d" % i}
                     for i, c in enumerate(kw.pop("champs", ["Jinx"]))]}
        cls.s.update(kw)
        cls.log.clear()

    @classmethod
    def now(cls):
        s = cls.s
        if s["paused"]:
            return s["t"]
        return s["t"] + (time.monotonic() - s["at"]) * cls.SCALE

    def _tick_recording(self):
        r, now = self.s["rec"], self.now()
        if r.get("recording") and now >= r["endTime"]:
            r["recording"] = False
            r["endTime"] = -1.0
            out = Path(r["path"])
            if self.s.get("alt_dir"):
                out = Path(self.s["alt_dir"]) / "game-chose-this.webm"
                r["path"] = str(out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x1aE\xdf\xa3" + b"0" * 2000)

    def do_GET(self):
        s = self.s
        with self.lock:
            self.log.append(("GET", self.path, None))
            self._tick_recording()
            if self.path.startswith("/replay/") or \
                    self.path.startswith("/liveclientdata"):
                if not s["open"]:
                    if s["watch_polls"]:
                        s["watch_polls"] -= 1
                        if not s["watch_polls"]:
                            s["open"] = True
                    return self._send({"error": "loading"}, 404)
            if self.path == "/replay/playback":
                return self._send({"time": self.now(), "length": 1800.0,
                                   "paused": s["paused"], "speed": 1.0,
                                   "seeking": False})
            if self.path == "/replay/render":
                return self._send(s["render"])
            if self.path == "/replay/recording":
                return self._send(dict(s["rec"], currentTime=self.now()))
            if self.path == "/liveclientdata/playerlist":
                return self._send(s["players"])
            if self.path.startswith("/lol-replays/v1/metadata/"):
                st = s["state"]
                if st == "downloading":
                    s["state"] = "watch"
                return self._send({"state": st, "downloadProgress": 50})
            if self.path == "/lol-replays/v1/configuration":
                return self._send({"isReplaysEnabled": True})
            self._send({"message": "nope"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        s = self.s
        with self.lock:
            self._tick_recording()
            self.log.append(("POST", self.path, body,
                             bool(s["rec"].get("recording")), self.now()))
            if self.path == "/replay/playback":
                s["t"], s["at"] = self.now(), time.monotonic()
                if "time" in body:
                    s["t"] = body["time"]
                    s["render"].update(selectionName="")
                if "paused" in body:
                    s["paused"] = body["paused"]
                return self._send({})
            if self.path == "/replay/render":
                if "selectionName" in body:
                    want = re.sub(r"\W", "", body["selectionName"].lower())
                    hit = [p["championName"] for p in s["players"]
                           if p["championName"].lower() == want]
                    body = dict(body, selectionName=hit[0] if hit else "")
                s["render"].update(body)
                return self._send(s["render"])
            if self.path == "/replay/recording":
                if not body.get("recording"):
                    s["rec"] = dict(s["rec"], recording=False)
                elif s.get("hidden"):
                    s["rec"] = dict(body, recording=False, width=16,
                                    height=0)
                else:
                    if body.get("startTime", -1) >= 0:      # it seeks
                        s["t"], s["at"] = body["startTime"], time.monotonic()
                        s["render"].update(selectionName="")
                    s["rec"] = dict(body, recording=True, width=1440,
                                    height=900)
                    s["rec_from"] = self.now()
                return self._send(s["rec"])
            if self.path.endswith("/download/graceful"):
                s["state"] = "downloading"
            elif self.path.endswith("/watch"):
                s["watch_polls"] = 3            # 404s while it loads
            self._send(None, 204)


@pytest.fixture(scope="module")
def fake(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    key, crt = d / "k.pem", d / "c.pem"
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-nodes", "-keyout", str(key), "-out", str(crt),
                        "-days", "1", "-subj", "/CN=127.0.0.1"],
                       capture_output=True)
    if r.returncode:
        pytest.skip("openssl not available")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _client(port, folder: Path) -> local.LeagueClient:
    (folder / "lockfile").write_text("LeagueClient:1:%d:pw:https" % port)
    return local.LeagueClient(folder)


def _no_camera_or_seek_while_recording():
    bad = [x for x in _Fake.log if x[0] == "POST" and x[3]
           and x[1] in ("/replay/playback", "/replay/render")]
    bad += [x for x in _Fake.log if x[0] == "POST" and x[1] ==
            "/replay/recording" and x[2].get("startTime", -1) >= 0]
    assert not bad, bad


# ================================================================ recorder
def _rec(port, *a, **k):
    """A Recorder that never touches a real League window."""
    k.setdefault("poll", 0.05)
    k.setdefault("front", lambda: True)
    k.setdefault("visible", lambda: True)
    return Recorder(local.ReplayAPI(port), *a, **k)


def test_record_clips(fake, tmp_path):
    _Fake.reset()
    events = []
    rec = _rec(fake, "Jinx", "x0", on_progress=events.append)
    clips = [Clip(100, 112, tmp_path / "g" / "1m_kill.webm", "Kill"),
             Clip(300, 320, tmp_path / "g" / "5m_baron.webm", "Baron")]
    res = rec.record_all(clips)
    assert [r.ok for r in res] == [True, True], res
    for c in clips:
        assert c.target.read_bytes().startswith(b"\x1aE\xdf\xa3")
    starts = [(x[2], x[4]) for x in _Fake.log
              if x[0] == "POST" and x[1] == "/replay/recording"
              and x[2].get("recording")]
    # verified live: startTime -1 (= now, no seek that drops the camera
    # lock), no width/height (= the window), enforceFrameRate off (on made
    # clips ~1.3x too fast)
    assert starts[0][0] == {"recording": True, "startTime": -1.0,
                            "endTime": 112.0, "codec": "webm",
                            "framesPerSecond": 60, "enforceFrameRate": False,
                            "lossless": False, "replaySpeed": 1.0,
                            "path": str(clips[0].target)}
    # started when the replay reached the clip, not before (and within
    # ~0.5 s of real time: the fake's clock runs SCALE x)
    for (b, t), c in zip(starts, clips):
        assert c.start_s - 0.1 <= t < c.start_s + 0.5 * _Fake.SCALE
    seeks = [b["time"] for m, p, b, *_ in _Fake.log
             if m == "POST" and p == "/replay/playback" and "time" in b]
    assert seeks == [100 - recorder.PREROLL_S, 300 - recorder.PREROLL_S]
    _no_camera_or_seek_while_recording()
    # the camera was locked on me after each seek, before recording
    assert sum(1 for m, p, b, *_ in _Fake.log if m == "POST"
               and p == "/replay/render" and b.get("selectionName")) >= 2
    phases = [e.phase for e in events]
    assert phases[-1] == "done" and "recording" in phases
    assert max(e.frac for e in events if e.phase == "recording") == 1.0
    assert _Fake.s["paused"]                         # paused when done


def test_record_moves_file_the_game_put_elsewhere(fake, tmp_path):
    _Fake.reset(alt_dir=str(tmp_path / "lol"))
    rec = _rec(fake, "Jinx", "")
    target = tmp_path / "out" / "2m_kill.webm"
    (r,) = rec.record_all([Clip(120, 130, target, "Kill")])
    assert r.ok and r.path == target and target.exists()
    assert not (tmp_path / "lol" / "game-chose-this.webm").exists()


def test_cancel_stops_the_recorder(fake, tmp_path):
    _Fake.reset()
    cancel = threading.Event()
    events = []

    def prog(p):
        events.append(p)
        if p.phase == "recording" and p.frac > 0.2:
            cancel.set()
    rec = _rec(fake, "Jinx", "", on_progress=prog, cancel=cancel)
    res = rec.record_all([Clip(100, 160, tmp_path / "a.webm"),
                          Clip(400, 420, tmp_path / "b.webm")])
    assert res == [] and events[-1].phase == "cancelled"
    assert _Fake.log[-1][1:3] == ("/replay/recording", {"recording": False})
    assert not (tmp_path / "b.webm").exists()


def test_hidden_window_is_brought_up_or_waited_for(fake, tmp_path,
                                                   monkeypatch):
    """A full-screen League in the background records nothing: bring it to
    the front first; if that fails, wait for the user (then give up)."""
    _Fake.reset()
    shown = {"n": 0}

    def visible():
        shown["n"] += 1
        return shown["n"] > 3            # the user clicks it after a while
    events = []
    rec = _rec(fake, "Jinx", "", front=lambda: False, visible=visible,
               on_progress=events.append)
    (r,) = rec.record_all([Clip(100, 105, tmp_path / "a.webm")])
    assert r.ok and "window" in [e.phase for e in events]
    monkeypatch.setattr(recorder, "WINDOW_WAIT_S", 0.3)
    rec = _rec(fake, "Jinx", "", front=lambda: False, visible=lambda: False)
    (r,) = rec.record_all([Clip(200, 205, tmp_path / "b.webm")])
    assert not r.ok and "hidden" in r.error


def test_recorder_that_never_starts(fake, tmp_path):
    """The game said OK but isn't recording (window hidden after all):
    fail the clip with the reason instead of waiting for a file."""
    _Fake.reset(hidden=True)
    (r,) = _rec(fake, "Jinx", "").record_all([Clip(100, 105,
                                                   tmp_path / "a.webm")])
    assert not r.ok and "didn't start recording" in r.error
    assert "16x0" in r.error


def test_open_replay_downloads_and_waits(fake, tmp_path):
    _Fake.reset(open=False, state="download", champs=["Jinx", "Draven"])
    lc = _client(fake, tmp_path)
    msgs = []
    recorder.open_replay(lc, local.ReplayAPI(fake), 42, {}, {"Jinx"},
                         threading.Event(), msgs.append, timeout=10,
                         poll=0.02)
    posts = [p for m, p, *_ in _Fake.log if m == "POST"]
    assert posts == ["/lol-replays/v1/rofls/42/download/graceful",
                     "/lol-replays/v1/rofls/42/watch"]
    assert msgs[0] == "Downloading the replay…" and msgs[-1] == "Replay open"
    # already open with these champions: nothing to do
    _Fake.log.clear()
    recorder.open_replay(lc, local.ReplayAPI(fake), 42, {}, {"Draven"},
                         threading.Event(), timeout=5, poll=0.02)
    assert not [x for x in _Fake.log if x[0] == "POST"]


def test_open_replay_old_patch(fake, tmp_path):
    _Fake.reset(open=False, state="incompatible")
    with pytest.raises(recorder.NotRecordable, match="Old patch"):
        recorder.open_replay(_client(fake, tmp_path),
                             local.ReplayAPI(fake), 42, {}, {"Jinx"},
                             threading.Event(), timeout=5, poll=0.02)


def test_open_replay_no_client(tmp_path):
    with pytest.raises(local.NotRunning):
        recorder.open_replay(local.LeagueClient(tmp_path),
                             local.ReplayAPI(1), 42, {}, {"Jinx"},
                             threading.Event(), timeout=1, poll=0.01)


# ===================================================================== UI
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """ONE App (one Tk root) for every UI test in this module."""
    import tkinter as tk
    tmp = tmp_path_factory.mktemp("hl-ui")
    mp = pytest.MonkeyPatch()
    mp.setenv("REVIEW_DATA_DIR", str(tmp))
    from review import config
    config._dotenv.cache_clear()
    import review.static as static

    def offline(*a, **k):
        raise OSError("offline in tests")
    mp.setattr(static.urllib.request, "urlopen", offline)
    mp.setattr(static, "_CHAMPS", None)
    mp.setattr("tempfile.mkdtemp", lambda **k: str(tmp))
    import highlights.app as happ
    opened = []
    mp.setattr(happ, "open_path", opened.append)
    from highlights import gamewindow
    mp.setattr(gamewindow, "bring_to_front", lambda *a, **k: True)
    mp.setattr(gamewindow, "visible", lambda *a, **k: True)
    mp.setattr(happ, "_simulate", functools.partial(
        happ._simulate, step=0.005, per_clip=0.05))
    from highlights.__main__ import _demo_data
    player = _demo_data()
    first = player.pop("open")
    try:
        a = happ.App(demo=True)
    except tk.TclError as e:
        mp.undo()
        pytest.skip("no display: %s" % e)
    errors = []
    a.root.report_callback_exception = lambda *exc: errors.append(exc)
    a.install = tmp
    a.lcu = local.LeagueClient(tmp)
    a.rapi = local.ReplayAPI(1)
    a.player = player
    a.reload_games(then=lambda: a.open_game(first))
    a._opened, a._errors, a._tmp = opened, errors, tmp
    yield a
    try:
        a.root.destroy()
    except tk.TclError:
        pass
    mp.undo()


def _pump(a, secs=1.5, until=None):
    end = time.time() + secs
    while time.time() < end:
        a.root.update()
        if until and until():
            return True
        time.sleep(0.02)
    return until() if until else True


class _Ev:
    """A stand-in for a Tk mouse event."""
    def __init__(self, x, y):
        self.x, self.y, self.x_root, self.y_root = int(x), int(y), 0, 0


def test_ui_editor_trim_and_demo_save(app):
    from highlights.timeline import MIN_CLIP_MS
    a = app
    assert _pump(a, 8, lambda: a.game is not None and a.tl is not None)
    assert a.game.me.champion == "Sona"
    best = max(range(len(a.hl)), key=lambda i: a.hl[i].score)
    assert a.sel_i == best and a.tl.selected == best
    assert len(a.tl.spans) == len(a.hl)
    # picking a block: selected, no replay -> a status line, no crash
    a.pick(0)
    assert a.sel_i == 0 and "Demo" in a.status.cget("text")
    a.step(1)
    assert a.sel_i == 1
    a.step(-2)
    assert a.sel_i == len(a.hl) - 1
    a.pick(best)
    # trimming: edges, nudges, clamps, minimum length, reset
    h = a.hl[best]
    a.trim(best, h.start_ms - 2000, h.end_ms + 3000)
    assert a.clip_range(best) == (h.start_ms - 2000, h.end_ms + 3000)
    assert a.tl.spans[best].end_ms == h.end_ms + 3000
    a.nudge_edge("in", 1000)
    assert a.clip_range(best)[0] == h.start_ms - 1000
    a.trim(best, -5000, 10 ** 9)
    assert a.clip_range(best) == (0, a.game.duration_s * 1000)
    a.trim(best, 100_000, 100_500)
    lo, hi = a.clip_range(best)
    assert hi - lo == MIN_CLIP_MS
    a.reset_trim()
    assert best not in a.trims and a.clip_range(best) == (h.start_ms,
                                                          h.end_ms)
    # dragging the selected block's right edge on the timeline
    _pump(a, 0.3)
    tl = a.tl
    y = tl.winfo_height() and __import__("review.theme.widgets",
                                         fromlist=["px"]).px(tl.CLIPS)
    x_out = tl.x_of(h.end_ms)
    tl._press(_Ev(x_out, y))
    assert tl._drag == "out"
    tl._drag_to(_Ev(tl.x_of(h.end_ms + 6000), y))
    tl._release(_Ev(tl.x_of(h.end_ms + 6000), y))
    lo, hi = a.clip_range(best)
    assert lo == h.start_ms and abs(hi - (h.end_ms + 6000)) <= 1500
    a.reset_trim()
    # clicking another block picks it; I/O without a replay -> a hint
    other = next(i for i in range(len(a.hl)) if i != best)
    o = a.hl[other]
    tl._press(_Ev((tl.x_of(o.start_ms) + tl.x_of(o.end_ms)) / 2, y))
    assert a.sel_i == other
    a.set_edge("in")
    assert "Open the replay first" in a.status.cget("text")
    a.pick(best)
    # demo save: simulated, nothing written
    a.save()
    assert a.rec is not None and a.tl.locked
    a.pick(other)                           # ignored while saving
    assert a.sel_i == best
    assert _pump(a, 10, lambda: a.rec is None)
    assert "Demo" in a.status.cget("text") and not a.tl.locked
    assert not any((a._tmp / "Highlights").rglob("*.webm"))
    for v in ("clips", "editor"):
        a.navigate(v)
        _pump(a, 0.3)
    assert not a._errors, a._errors


def test_ui_old_patch_cannot_save(app):
    a = app
    assert _pump(a, 8, lambda: a.game is not None)
    a.demo = False
    try:
        a.current_patch = "99.1"
        assert "Old patch" in a._replay_line()[0]
        a.save()
        assert a.rec is None and "old patch" in a.status.cget("text")
        a.pick_game()
        _pump(a, 0.3)
        rows = a._games_table.rows
        assert rows and all(r.cells["rec"][0] == "old patch" for r in rows)
        a._games_dialog.destroy()
    finally:
        a.demo = True
        a.current_patch = None


def test_ui_watch_jump_trim_and_save(app, fake):
    """Against the fake game: Watch opens the replay, a block jumps there
    with the camera on you, O sets the out point at the playhead, Save
    records exactly in..out, and the clip shows up as saved."""
    a = app
    assert _pump(a, 8, lambda: a.game is not None and a.tl is not None)
    g = a.game
    _Fake.reset(open=False, state="watch",
                champs=sorted({p.champion for p in g.players.values()}))
    _Fake.SCALE = 60.0
    a.demo = False
    a.current_patch = g.patch
    a.rapi = local.ReplayAPI(fake)
    a.lcu = _client(fake, a._tmp)
    try:
        a.open_replay()
        assert _pump(a, 15, lambda: a.this_open and not a.opening),             a.status.cget("text")
        assert ("/lol-replays/v1/rofls/%d/watch" % g.game_id) in [
            x[1] for x in _Fake.log if x[0] == "POST"]
        a._poll_local()
        assert _pump(a, 4, lambda: a.tl.playhead_ms is not None)
        best = a.sel_i
        lo, hi = a.clip_range(best)
        _Fake.log.clear()
        a.pick(best)
        assert _pump(a, 4, lambda: any(
            x[0] == "POST" and x[1] == "/replay/playback" and "time" in x[2]
            for x in _Fake.log))
        seek = next(x[2] for x in _Fake.log if x[0] == "POST"
                    and x[1] == "/replay/playback" and "time" in x[2])
        assert seek["time"] == lo / 1000
        # O at the playhead: out = where the replay is now
        a.playback = {"time": lo / 1000 + 5, "paused": True, "speed": 1.0}
        a._pb_at = time.monotonic()
        a.set_edge("out")
        assert a.clip_range(best) == (lo, lo + 5000)
        # preview pauses at the out point
        _Fake.log.clear()
        a.preview()
        assert a.preview_out == lo + 5000
        assert _pump(a, 6, lambda: any(
            x[0] == "POST" and x[1] == "/replay/playback"
            and x[2] == {"paused": True} for x in _Fake.log))
        # save exactly in..out
        _Fake.log.clear()
        a.save()
        assert _pump(a, 30, lambda: a.rec is None), a.status.cget("text")
        assert [r.ok for r in a.results] == [True], a.results
        assert a.clip_path(best).exists()
        assert "Saved" in a.status.cget("text")
        start = next(x[2] for x in _Fake.log if x[0] == "POST"
                     and x[1] == "/replay/recording" and x[2].get(
                         "recording"))
        assert start["startTime"] == -1.0
        assert start["endTime"] == (lo + 5000) / 1000
        _no_camera_or_seek_while_recording()
        assert a.tl.spans[best].saved
        a.play(a.clip_path(best))
        assert a._opened[-1] == a.clip_path(best)
        assert not a._errors, a._errors
    finally:
        a.demo = True
        a.this_open = False
        _Fake.SCALE = 25.0
