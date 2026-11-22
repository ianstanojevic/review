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
    """Replay API + LCU in one. Replay time runs SCALE× wall time; the
    recorder writes its file when the clock passes endTime (to ``path``, or
    to ``alt_dir`` like a game that ignores the path)."""
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
                             bool(s["rec"].get("recording"))))
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
                s["rec"] = dict(body) if body.get("recording") else dict(
                    s["rec"], recording=False)
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
    assert not bad, bad


# ================================================================ recorder
def test_record_clips(fake, tmp_path):
    _Fake.reset()
    events = []
    rec = Recorder(local.ReplayAPI(fake), "Jinx", "x0", 1280, 720, 30,
                   on_progress=events.append, poll=0.05)
    clips = [Clip(100, 112, tmp_path / "g" / "1m_kill.webm", "Kill"),
             Clip(300, 320, tmp_path / "g" / "5m_baron.webm", "Baron")]
    res = rec.record_all(clips)
    assert [r.ok for r in res] == [True, True], res
    for c in clips:
        assert c.target.read_bytes().startswith(b"\x1aE\xdf\xa3")
    starts = [b for m, p, b, *_ in _Fake.log
              if m == "POST" and p == "/replay/recording"
              and b.get("recording")]
    assert starts[0] == {"recording": True, "startTime": 100.0,
                         "endTime": 112.0, "codec": "webm",
                         "framesPerSecond": 30, "width": 1280,
                         "height": 720, "enforceFrameRate": True,
                         "lossless": False, "replaySpeed": 1.0,
                         "path": str(clips[0].target)}
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
    rec = Recorder(local.ReplayAPI(fake), "Jinx", "", poll=0.05)
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
    rec = Recorder(local.ReplayAPI(fake), "Jinx", "", on_progress=prog,
                   cancel=cancel, poll=0.05)
    res = rec.record_all([Clip(100, 160, tmp_path / "a.webm"),
                          Clip(400, 420, tmp_path / "b.webm")])
    assert res == [] and events[-1].phase == "cancelled"
    assert _Fake.log[-1][1:3] == ("/replay/recording", {"recording": False})
    assert not (tmp_path / "b.webm").exists()


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


def test_ui_highlights_and_demo_recording(app):
    a = app
    assert _pump(a, 8, lambda: a.game is not None and a.hl)
    assert a.game.me.champion == "Sona"
    assert a.sel == score.default_selection(a.hl)
    best = max(range(len(a.hl)), key=lambda i: a.hl[i].score)
    assert best in a.sel
    # order: best first, then in game order
    assert a._order()[0] == best
    a._set_order("time")
    assert a._order() == list(range(len(a.hl)))
    a._set_order("best")
    # tick / untick / bulk
    a.toggle(best)
    assert best not in a.sel
    a.toggle(best)
    a._select(set())
    assert a.selected() == []
    a._select({best})
    assert "about" in [w for w in a.actions.winfo_children()][0] \
        .winfo_children()[0].cget("text")
    # demo: simulated, nothing written
    a.record()
    assert a.rec is not None
    assert _pump(a, 10, lambda: a.rec is None)
    assert "Demo" in a.status.cget("text")
    assert not any((a._tmp / "Highlights").rglob("*.webm"))
    assert not a._errors, a._errors


def test_ui_old_patch_cannot_record(app):
    a = app
    assert _pump(a, 8, lambda: a.game is not None)
    a.demo = False
    try:
        a.current_patch = "99.1"
        assert not a._can_record()[0]
        a.record()
        assert a.rec is None and "current patch" in a.status.cget("text")
        a._fill_games()
        assert all(r.cells["rec"][0] == "old patch"
                   for r in a.games_tbl.rows)
    finally:
        a.demo = True
        a.current_patch = None
        a._fill_games()


def test_ui_records_and_lists_clips(app, fake):
    a = app
    assert _pump(a, 8, lambda: a.game is not None and a.hl)
    g = a.game
    _Fake.reset(champs=sorted({p.champion for p in g.players.values()}))
    _Fake.SCALE = 60.0
    a.demo = False
    a.current_patch = g.patch
    a.rapi = local.ReplayAPI(fake)
    a.lcu = _client(fake, a._tmp)
    a.replay_state = {"state": "watch", "open": True}
    try:
        two = sorted(range(len(a.hl)), key=lambda i: -a.hl[i].score)[:2]
        a._select(set(two))
        a.record()
        assert a.rec is not None
        assert _pump(a, 30, lambda: a.rec is None), a.status.cget("text")
        assert [r.ok for r in a.results] == [True, True], a.results
        folder = a.out_dir() / score.folder_name(g)
        files = sorted(f.name for f in folder.glob("*.webm"))
        names = score.file_names(a.hl)
        assert files == sorted(names[i] for i in two)
        assert "Recorded 2 clips" in a.status.cget("text")
        _no_camera_or_seek_while_recording()
        # the recorded list plays a clip on click
        a.play(folder / files[0])
        assert a._opened[-1] == folder / files[0]
        a.navigate("clips")
        _pump(a, 0.3)
        a.navigate("record")
        assert _pump(a, 3, lambda: a.game is not None)
        assert not a._errors, a._errors
    finally:
        a.demo = True
        _Fake.SCALE = 25.0
