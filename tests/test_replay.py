"""REPLAY: notes DB, game events, .rofl reading, game.cfg editing, the two
local APIs (against a fake HTTPS server) and a UI smoke test."""
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

from replay import local, rofl
from replay.events import build_game, context_lines
from replay.model import BOOKMARK, DEATH, MISSED
from replay.notes import Matchup, Notes

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def game_data():
    return json.load(gzip.open(FIX / "bot.json.gz"))


@pytest.fixture
def notes(tmp_path):
    n = Notes(tmp_path / "replay.sqlite")
    yield n
    n.close()


# ------------------------------------------------------------- events
def test_build_game_me_and_lane(game_data):
    g = build_game(game_data["match"], game_data["timeline"], "puuid-me")
    assert g.me.champion == "Kalista" and g.me.role == "BOTTOM"
    assert g.opponent.champion == "Vayne"
    assert g.me.team == 200
    assert g.ally_support.champion == "Renata"     # same team, UTILITY
    assert g.enemy_support.champion == "Karma"
    assert (g.kills, g.deaths, g.assists) == (1, 13, 1)
    assert len(g.deaths_list) == g.deaths == 13
    ts = [m.t_ms for m in g.moments]
    assert ts == sorted(ts)
    kinds = {m.kind for m in g.moments}
    assert {"death", "kill", "objective", "tower"} <= kinds


def test_death_context(game_data):
    g = build_game(game_data["match"], game_data["timeline"], "puuid-me")
    for d in g.deaths_list:
        assert d.x is not None and d.y is not None
        assert d.label.startswith("Killed by")
        assert d.detail["enemies"] >= 0
        lines = context_lines(d)
        assert lines and isinstance(lines[0], str)
    # 16:41 death in the demo: Nunu (jungler), alone
    nunu = [d for d in g.deaths_list if d.detail["killer"] == "Nunu"]
    assert nunu and all(d.detail["jungler"] for d in nunu)


def test_build_game_without_timeline(game_data):
    g = build_game(game_data["match"], None, "puuid-me")
    assert not g.has_timeline and g.moments == []


def test_build_game_wrong_player(game_data):
    with pytest.raises(ValueError):
        build_game(game_data["match"], None, "someone-else")


# -------------------------------------------------------------- notes
def test_deaths_are_idempotent(notes):
    notes.ensure_deaths("M1", "p", [1000, 2000], "Jinx")
    notes.ensure_deaths("M1", "p", [1000, 2000, 3000], "Jinx")
    ms = notes.marks("M1", "p")
    assert [m.t_ms for m in ms] == [1000, 2000, 3000]
    assert all(m.kind == DEATH and not m.tagged for m in ms)


def test_tagging_and_counts(notes):
    notes.ensure_deaths("M1", "p", [1000, 2000, 3000], "Jinx")
    a, b, c = notes.marks("M1", "p")
    notes.update_mark(a.id, category="gank", avoidable=1, note="ward!")
    notes.update_mark(b.id, category="gank", avoidable=0)
    notes.update_mark(c.id, category="vision")
    notes.update_mark(c.id, bogus="x")            # ignored, no crash
    got = notes.get_mark(a.id)
    assert got.category == "gank" and got.avoidable == 1 and got.note == "ward!"
    assert notes.category_counts("p", DEATH) == {"gank": (2, 1),
                                                 "vision": (1, 0)}
    assert notes.category_counts("p", DEATH, champion="Ashe") == {}
    assert notes.category_counts("p", DEATH, match_ids=[]) == {}
    assert notes.tagged_counts("p") == (3, 3, 1)
    assert notes.progress("p") == {"M1": (3, 3)}


def test_own_marks(notes):
    notes.ensure_deaths("M1", "p", [5000])
    mk = notes.add_mark("M1", "p", MISSED, 9000, "Jinx", "wave", "big wave")
    bm = notes.add_mark("M1", "p", BOOKMARK, 1000)
    assert [m.kind for m in notes.marks("M1", "p")] == [BOOKMARK, DEATH,
                                                        MISSED]
    assert bm.tagged and mk.tagged
    assert notes.progress("p")["M1"] == (2, 3)    # death untagged
    with pytest.raises(ValueError):
        notes.add_mark("M1", "p", DEATH, 1)
    death = [m for m in notes.marks("M1", "p") if m.kind == DEATH][0]
    notes.delete_mark(death.id)                   # deaths can't be deleted
    notes.delete_mark(mk.id)
    assert {m.kind for m in notes.marks("M1", "p")} == {DEATH, BOOKMARK}


def test_matchups(notes):
    notes.save_matchup(Matchup("M1", "p", "Jinx", "Draven", "Janna",
                               "Braum", 2, "respect lvl 2"))
    notes.save_matchup(Matchup("M2", "p", "Ashe", "Draven", lane=4))
    notes.save_matchup(Matchup("M3", "p", "Ashe", "Draven"))   # empty
    m = notes.matchup("M1", "p")
    assert m.lane == 2 and m.note == "respect lvl 2" and not m.reviewed
    m.reviewed = True
    notes.save_matchup(m)
    assert notes.reviewed_games("p") == {"M1"}
    h = notes.matchup_history(None, "Draven", exclude_match="M2")
    assert [x.match_id for x in h] == ["M1"]
    assert {x.match_id for x in notes.matchup_history(None, "Draven")} == {
        "M1", "M2"}
    assert [x.match_id for x in notes.matchup_history("Ashe", "Draven")] \
        == ["M2"]
    assert {x.match_id for x in notes.all_matchups("p")} == {"M1", "M2"}


# --------------------------------------------------------------- rofl
def _fake_rofl(path: Path, version="16.23.839.9929", length=1668391):
    stats = [{"RIOT_ID_GAME_NAME": "Me", "SKIN": "Jinx", "WIN": "Win"}]
    meta = json.dumps({"gameLength": length, "lastGameChunkId": 1,
                       "lastKeyFrameId": 1,
                       "statsJson": json.dumps(stats)})
    path.write_bytes(b"RIOT\x02\x00\x8b\xbf" + b"\x0e" + version.encode()
                     + b"\x01\x00" + bytes(range(256)) * 20 + meta.encode())


def test_rofl_scan_and_read(tmp_path):
    _fake_rofl(tmp_path / "EUW1-8050970813.rofl")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "EUW1-bad.rofl").write_text("x")
    found = rofl.scan(tmp_path)
    assert list(found) == ["EUW1_8050970813"]
    f = found["EUW1_8050970813"]
    assert f.game_id == 8050970813 and f.patch == "16.23"
    md = rofl.read_metadata(f.path)
    assert md["gameLength"] == 1668391 and md["stats"][0]["SKIN"] == "Jinx"
    assert rofl.scan(tmp_path / "missing") == {}


# ----------------------------------------------------------- game.cfg
CFG = ("[General]\r\nEnableReplayApi=0\r\nEnableScreenShake=1\r\n"
       "[HUD]\r\nShowTimestamps=0\r\n")


def test_enable_replay_api(tmp_path):
    (tmp_path / "Config").mkdir()
    cfg = tmp_path / "Config" / "game.cfg"
    cfg.write_bytes(CFG.encode())
    assert local.replay_api_enabled(tmp_path) is False
    assert local.enable_replay_api(tmp_path)
    assert local.replay_api_enabled(tmp_path) is True
    out = cfg.read_bytes().decode()
    assert out == CFG.replace("EnableReplayApi=0", "EnableReplayApi=1")
    bak = tmp_path / "Config" / "game.cfg.bak-before-replayapi"
    assert bak.read_bytes().decode() == CFG
    assert local.enable_replay_api(tmp_path)          # already on: no-op


def test_enable_replay_api_missing_key(tmp_path):
    (tmp_path / "Config").mkdir()
    cfg = tmp_path / "Config" / "game.cfg"
    cfg.write_bytes(b"[General]\r\nWindowMode=0\r\n")
    assert local.enable_replay_api(tmp_path)
    assert cfg.read_bytes() == (b"[General]\r\nEnableReplayApi=1\r\n"
                                b"WindowMode=0\r\n")
    assert local.replay_api_enabled(tmp_path / "nope") is None


# ------------------------------------------- local APIs (fake server)
class _Fake(http.server.BaseHTTPRequestHandler):
    state = {}
    log = []

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.log.append(("GET", self.path, self.headers.get(
            "Authorization")))
        s = self.state
        if self.path == "/replay/playback":
            self._send(s["pb"])
        elif self.path == "/replay/render":
            self._send(s["render"])
        elif self.path == "/liveclientdata/playerlist":
            self._send(s.get("players", []))
        elif self.path.startswith("/lol-replays/v1/metadata/"):
            # like the real client (16.23): 404 until the game is known,
            # junk downloadProgress outside "downloading"
            gid = int(self.path.rsplit("/", 1)[1])
            if gid in s.get("known", (8050970813,)):
                self._send({"gameId": gid, "state": "watch",
                            "downloadProgress": 2349731080})
            else:
                self._send({"errorCode": "RPC_ERROR", "httpStatus": 404}, 404)
        elif self.path == "/lol-replays/v1/configuration":
            self._send({"isReplaysEnabled": True})
        else:
            self._send({"message": "nope"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.log.append(("POST", self.path, body))
        s = self.state
        if self.path == "/replay/playback":
            s["pb"].update(body)
            self._send(s["pb"])
        elif self.path == "/replay/render":
            if "selectionName" in body:
                # like the game (16.23): any spelling of a selectable name,
                # read back in display form; unknown names (Riot IDs) -> ""
                key = re.sub(r"[^0-9a-z]", "", body["selectionName"].lower())
                disp = {re.sub(r"[^0-9a-z]", "", n.lower()): n
                        for n in s.get("selectable", ())}
                body = dict(body, selectionName=disp.get(key, ""))
            s["render"].update(body)
            self._send(s["render"])
        else:
            if self.path.startswith("/lol-replays/v2/metadata/"):
                s.setdefault("known", [8050970813]).append(
                    int(self.path.split("/")[4]))
            self._send(None, 204)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
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


def test_replay_api_playback(server):
    _Fake.state = {"pb": {"time": 100.0, "paused": False, "speed": 1.0,
                          "length": 1800.0, "seeking": False},
                   "render": {"cameraAttached": False}}
    api = local.ReplayAPI(server)
    assert api.playback()["time"] == 100.0
    api.set_playback(time=-5, paused=True, speed=0.5)
    pb = api.playback()
    assert pb["time"] == 0.0 and pb["paused"] and pb["speed"] == 0.5


def test_follow_falls_back_to_attached(server):
    _Fake.state = {"pb": {}, "render": {"cameraAttached": False}}
    api = local.ReplayAPI(server)
    assert api.follow("Jinx", "Brödbakarn") == "attached"
    assert _Fake.state["render"]["cameraAttached"] is True


def test_follow_locks_by_name(server):
    _Fake.state = {"pb": {}, "render": {"cameraAttached": False,
                                        "selectionName": ""},
                   "selectable": ("Brödbakarn",),
                   "players": [{"championName": "Jinx",
                                "riotIdGameName": "Brödbakarn"}]}
    api = local.ReplayAPI(server)
    assert api.follow("Jinx", "Brödbakarn") == "locked"


def test_replay_api_not_running():
    api = local.ReplayAPI(1)                     # nothing listens there
    with pytest.raises(local.NotRunning):
        api.playback()
    assert api.follow("Jinx") == "failed"


def test_league_client(server, tmp_path):
    lc = local.LeagueClient(tmp_path)
    assert not lc.running()                       # no lockfile
    with pytest.raises(local.NotRunning):
        lc.metadata(1)
    (tmp_path / "lockfile").write_text(
        "LeagueClient:1234:%d:s3cret:https" % server)
    _Fake.log.clear()
    assert lc.running()
    md = lc.metadata(8050970813)
    assert md["state"] == "watch" and "downloadProgress" not in md
    lc.download(8050970813)
    lc.watch(8050970813)
    gets = [x for x in _Fake.log if x[0] == "GET"]
    import base64
    assert gets[0][2] == "Basic " + base64.b64encode(
        b"riot:s3cret").decode()
    posts = [(p, b) for m, p, b in _Fake.log if m == "POST"]
    assert posts == [
        ("/lol-replays/v1/rofls/8050970813/download/graceful",
         local.COMPONENT),
        ("/lol-replays/v1/rofls/8050970813/watch", local.COMPONENT)]


# ----------------------------------------------------------------- UI
@pytest.fixture
def ui(tmp_path, monkeypatch):
    import tkinter as tk
    monkeypatch.setenv("REVIEW_DATA_DIR", str(tmp_path))
    from review import config
    config._dotenv.cache_clear()
    import review.static as static

    def offline(*a, **k):
        raise OSError("offline in tests")
    monkeypatch.setattr(static.urllib.request, "urlopen", offline)
    monkeypatch.setattr(static, "_CHAMPS", None)
    from replay.__main__ import _demo_data
    monkeypatch.setattr("tempfile.mkdtemp", lambda **k: str(tmp_path))
    player = _demo_data()
    from replay.app import App
    errors = []
    try:
        a = App(demo=True)
    except tk.TclError as e:
        pytest.skip("no display: %s" % e)
    a.root.report_callback_exception = lambda *exc: errors.append(exc)
    a.install = tmp_path                     # no real League here
    a.lcu = local.LeagueClient(tmp_path)
    a.rapi = local.ReplayAPI(1)
    a.set_player(player)
    yield a, errors
    try:
        a.root.destroy()
    except tk.TclError:
        pass


def _pump(app, secs=1.5, until=None):
    end = time.time() + secs
    while time.time() < end:
        app.root.update()
        if until and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def test_ui(ui, server):
    """One window for both UI checks: a second Tk() in the same process
    comes up broken now and then on Windows (Python 3.14 / Tcl 8.6.15:
    'invalid command name "tcl_findLibrary"', or a window that never
    renders), which skipped or failed the second test ~4 runs in 10."""
    _ui_flow(*ui)
    _ui_with_replay_open(*ui, server)


def _ui_flow(a, errors):
    assert _pump(a, 8, lambda: a.game is not None and a.tl is not None)
    g = a.game
    assert g.me.champion == "Kalista" and len(a.marks) == 14
    # the first untagged death is selected; tag it via the editor path
    sel = a._mark(a.sel_mark)
    assert sel.kind == DEATH and not sel.tagged
    a._set(sel.id, category="vision", avoidable=1)
    assert a.notes.get_mark(sel.id).category == "vision"
    a._note_widget.insert("1.0", "ward river at 3:15")
    a._flush_note()
    assert a.notes.get_mark(sel.id).note == "ward river at 3:15"
    # next untagged moves on
    a.next_untagged()
    assert a.sel_mark != sel.id and not a._mark(a.sel_mark).tagged
    # a missed chance at the timeline cursor (no replay open)
    a.seek_cursor(600_000)
    n = len(a.marks)
    a.add_mark(MISSED)
    assert len(a.marks) == n + 1 and a._mark(a.sel_mark).kind == MISSED
    a.delete_mark(a.sel_mark)
    assert len(a.marks) == n
    # replay buttons without a replay: status message, no crash
    a.jump(60_000)
    a.toggle_pause()
    _pump(a, 0.5)
    for v in ("insights", "matchups", "review"):
        a.navigate(v)
        _pump(a, 0.6)
    assert not errors, errors


def _ui_with_replay_open(a, errors, server):
    """A replay is open (fake Replay API): the poll picks it up, a jump
    seeks to 15 s before the moment at the chosen speed, and a missed
    chance lands at the replay's current time."""
    assert _pump(a, 8, lambda: a.game is not None and a.tl is not None)
    _Fake.state = {"pb": {"time": 600.0, "paused": True, "speed": 1.0,
                          "length": 1700.0, "seeking": False},
                   "render": {"cameraAttached": False}}
    a.rapi = local.ReplayAPI(server)
    a._poll_local()
    assert _pump(a, 4, lambda: a.playback is not None)
    assert a.tl.playhead_ms == 600_000
    death = a.game.deaths_list[0]
    _Fake.log.clear()
    a.jump(death.t_ms)
    assert _pump(a, 3, lambda: any(m == "POST" and p == "/replay/playback"
                                   for m, p, _ in _Fake.log))
    body = next(b for m, p, b in _Fake.log
                if m == "POST" and p == "/replay/playback")
    assert body == {"time": death.t_ms / 1000 - 15, "paused": False,
                    "speed": 0.5}
    _pump(a, 0.3)
    a.playback = {"time": 600.0, "paused": True, "speed": 1.0}
    n = len(a.marks)
    a.add_mark(MISSED)
    mk = a._mark(a.sel_mark)
    assert len(a.marks) == n + 1 and mk.kind == MISSED and mk.t_ms == 600_000
    assert not errors, errors


def test_league_client_unseen_game(server, tmp_path):
    """The client answers 404 for a game it hasn't loaded; metadata() turns
    that into {} and create_metadata() sends exactly the 16.23 schema."""
    _Fake.state = {"pb": {}, "render": {}, "known": [8050970813]}
    (tmp_path / "lockfile").write_text(
        "LeagueClient:1234:%d:s3cret:https" % server)
    lc = local.LeagueClient(tmp_path)
    assert lc.metadata(7000000001) == {}
    _Fake.log.clear()
    lc.create_metadata(7000000001, {"info": {
        "gameEndTimestamp": 1763300000000, "gameType": "MATCHED_GAME",
        "gameVersion": "16.23.840.6595", "queueId": 420}})
    (post,) = [x for x in _Fake.log if x[0] == "POST"]
    assert post[1] == "/lol-replays/v2/metadata/7000000001/create"
    assert set(post[2]) == {"gameEnd", "gameType", "gameVersion", "queueId"}
    assert lc.metadata(7000000001)["state"] == "watch"


def test_follow_api_champion_name(server):
    """Riot's API says 'MissFortune', the replay says 'Miss Fortune' and
    rejects Riot IDs: still a lock (checked live on 16.23)."""
    _Fake.state = {"pb": {}, "render": {"cameraAttached": False,
                                        "selectionName": ""},
                   "selectable": ("Miss Fortune",),
                   "players": [{"championName": "Miss Fortune",
                                "rawChampionName":
                                "game_character_displayname_MissFortune",
                                "riotIdGameName": "Brödbakarn"}]}
    api = local.ReplayAPI(server)
    assert api.follow("MissFortune", "Brödbakarn") == "locked"
    assert _Fake.state["render"]["selectionName"] == "Miss Fortune"
    _Fake.state["render"]["selectionName"] = ""
    assert api.follow("MissFortune") == "locked"
