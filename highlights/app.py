"""HIGHLIGHTS desktop app — DRAFT's window, your best moments as videos.

Layout: banner (title · account · client status · ⚙), VIEW tabs, then:

* Record — your games (left) and the open game (right): its highlights
             with score, length and a tick box (the best ticked), "Record
             selected" with the estimated time, progress, and the clips
             already recorded for the game.
* Clips  — every clip you've recorded, newest first; click to play.

Matches and timelines come from Review's cache, like REPLAY. Recording goes
through League's own replay recorder (``recorder.py``). All network and
disk work runs off the UI thread and comes back through ``post``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path

from replay import local
from replay.app import QUEUE_SHORT, QUEUES, ROLE_SHORT, GameRow, _clear, _date
from replay.app import App as _Replay
from replay.app import Prefs as _ReplayPrefs
from replay.app import panel
from replay.events import GameView, build_game
from replay.model import fmt_time
from review import config, static
from review.cache import Cache
from review.theme import anim
from review.theme import widgets as W
from review.theme import window
from review.theme.tokens import (ACCENT, BAD, BORDER, DARK_BG, DIM, EDGE_HI,
                                 F_LG_B, F_SM, F_SM_B, F_XS, F_XS_B, FG, GOOD,
                                 HOVER, PANEL, PANEL2, SP_L, SP_M, SP_S, SP_XS,
                                 mix)
from review.theme.widgets import px
from review.ui.assets import IconStore

from . import recorder, score
from .recorder import Clip, ClipResult, Progress

VIEWS = [("record", "Record"), ("clips", "Clips")]
ORDERS = [("best", "Best first"), ("time", "In game order")]
LEGAL = ("HIGHLIGHTS isn't endorsed by Riot Games and doesn't reflect the "
         "views or opinions of Riot Games or anyone officially involved in "
         "producing or managing Riot Games properties.")


class Prefs(_ReplayPrefs):
    def __init__(self):
        self.path = config.data_dir() / "highlights_prefs.json"
        try:
            self.d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.d = {}


def open_path(p) -> None:
    """Play a clip / open a folder with what Windows uses for it."""
    if sys.platform == "win32":
        os.startfile(str(p))                       # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(p)])


def _mb(n: int) -> str:
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else "%d KB" % max(n // 1000, 1)


def _dur(s: float) -> str:
    s = int(round(s))
    return "%d min %02d s" % (s // 60, s % 60) if s >= 60 else "%d s" % s


class App:
    # REPLAY's account picker, game fetch and UI-thread queue, as they are
    ask_account = _Replay.ask_account
    switch = _Replay.switch
    fetch = _Replay.fetch
    _progress = _Replay._progress
    _known_players = _Replay._known_players
    post = _Replay.post
    _drain = _Replay._drain

    def __init__(self, demo: bool = False):
        import queue
        self.demo = demo
        self.root = root = W.make_root("Highlights")
        try:      # DRAFT's diamond with a record dot
            from PIL import ImageTk
            from .icon import icon_image
            root._icon = [ImageTk.PhotoImage(icon_image(s), master=root)
                          for s in (64, 48, 32, 16)]
            root.iconphoto(True, *root._icon)
        except Exception:
            pass
        self.tooltip = W.Tooltip(root)
        W.install_wheel(root)
        self.icons = IconStore(root)
        self.prefs = Prefs()
        self.cache = Cache(config.db_path())
        self.install = local.install_dir(self.prefs.get("league_dir"))
        self.lcu = local.LeagueClient(self.install)
        self.rapi = local.ReplayAPI()
        self.player: dict | None = None
        self.games: list[GameRow] = []
        self.current_patch: str | None = None
        self.game: GameView | None = None
        self.match: dict | None = None
        self.hl: list[score.Highlight] = []
        self.sel: set[int] = set()
        self.view = "record"
        self.client_ok = False
        self.replay_state: dict = {}
        self.job = None                    # Review's SyncJob (Fetch)
        self.rec: dict | None = None       # the recording in progress
        self.cancel: threading.Event | None = None
        self.results: list[ClipResult] = []
        self._inbox: queue.Queue = queue.Queue()
        self._alive = True
        self._build()
        self.root.after(30, self._drain)
        root.geometry(window.safe_geometry(
            root, self.prefs.get("geometry"), (1320, 900)))
        root.minsize(px(1080), px(680))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Destroy>", lambda e: e.widget is root and setattr(
            self, "_alive", False), add="+")
        root.report_callback_exception = lambda exc, val, tb: self._log(
            "".join(traceback.format_exception(exc, val, tb)))
        threading.Thread(target=self._load_static, daemon=True).start()
        if not demo:
            self._pick_player()
            self.root.after(400, self._poll_local)
        anim.fade_in(root)

    # ------------------------------------------------------------ layout
    def _build(self):
        outer = tk.Frame(self.root, bg=DARK_BG, padx=14, pady=12)
        outer.pack(fill="both", expand=True)
        self.banner = W.Banner(outer, "HIGHLIGHTS",
                               on_gear=self.show_settings)
        self.banner.pack(fill="x")
        nav = tk.Frame(outer, bg=DARK_BG)
        nav.pack(fill="x", pady=(SP_L, SP_M))
        self.nav = W.TabRow(nav, "VIEW", VIEWS, "record",
                            on_change=self.navigate)
        self.nav.pack(side="left")
        self.fetch_btn = W.button(nav, "Fetch new games", self.fetch,
                                  fg=DIM, font=F_SM)
        self.fetch_btn.configure(pady=4)
        self.fetch_btn.pack(side="right")
        acc = W.button(nav, "Account", self.ask_account, fg=DIM, font=F_SM)
        acc.configure(pady=4)
        acc.pack(side="right", padx=(0, SP_S))
        self.content = tk.Frame(outer, bg=DARK_BG)
        self.content.pack(fill="both", expand=True)
        bottom = tk.Frame(outer, bg=DARK_BG)
        bottom.pack(fill="x", side="bottom", pady=(SP_M, 0))
        tk.Label(bottom, text="Recording runs in real time · leave the "
                 "replay alone while a clip records", bg=DARK_BG,
                 fg=mix(DIM, DARK_BG, 0.35), font=F_XS).pack(side="left",
                                                             anchor="s")
        self.status = tk.Label(bottom, text="", bg=DARK_BG, fg=DIM, font=F_SM)
        self.status.pack(side="right", anchor="s")
        self.navigate("record")

    def set_status(self, text, color=DIM):
        self.status.configure(text=text, fg=color)

    def _banner(self):
        bits = []
        if self.player:
            bits.append("%s · %s" % (self.player["riot_id"],
                                     self.player["region"]))
        if self.demo:
            bits.append("demo — nothing is recorded")
        else:
            bits.append("client " + ("connected" if self.client_ok
                                     else "not running"))
        self.banner.set_context(" · ".join(bits))

    def navigate(self, name):
        self.view = name
        self.nav.select(name, fire=False)
        _clear(self.content)
        getattr(self, "_view_" + name)()

    def out_dir(self) -> Path:
        return Path(self.prefs.get("out_dir") or recorder.default_out_dir())

    # -------------------------------------------------------- data in
    def _load_static(self):
        try:
            static.refresh_champions()
            ver = static.latest_version()
        except Exception:
            ver = None

        def done():
            if ver:
                self.current_patch = ".".join(ver.split(".")[:2])
                if self.view == "record":
                    self._fill_games()
                    self._render_game()
        self.post(done)

    def _pick_player(self):
        saved = self.prefs.get("player")
        known = self._known_players()
        if saved and any(k["puuid"] == saved.get("puuid") for k in known):
            self.set_player(saved)
        elif known:
            self.set_player(known[0])
        else:
            self.set_status("No account yet — click Account and enter "
                            "your Riot ID.", EDGE_HI)
            self._banner()

    def set_player(self, p: dict):
        self.player = p
        self.prefs.set("player", p)
        self._banner()
        self.reload_games()

    def reload_games(self, then=None):
        if not self.player:
            return
        puuid = self.player["puuid"]
        self.set_status("Loading games…")

        def work():
            rows = []
            for mid in self.cache.player_match_ids(puuid, QUEUES, limit=150):
                m = self.cache.get_match(mid)
                if m:
                    try:
                        rows.append(GameRow(m, puuid))
                    except (StopIteration, KeyError, ValueError):
                        pass
            self.post(done, rows)

        def done(rows):
            self.games = rows
            self.set_status("%d games" % len(rows))
            if self.view == "record":
                self._fill_games()
            if then:
                then()
            elif self.game is None and rows:
                self.open_game(rows[0].match_id)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------- recordable or not
    def _patch_of(self, version: str) -> str:
        return ".".join(version.split(".")[:2])

    def old_patch(self, version: str) -> bool:
        """Riot keeps replays for the current patch only."""
        if self.demo or not self.current_patch:
            return False
        return self._patch_of(version) != self.current_patch

    def _clip_count(self, g: GameRow) -> int:
        d = self.out_dir() / score.folder_for(g.created, g.champion,
                                              g.opponent)
        try:
            return sum(1 for f in d.iterdir() if f.suffix == ".webm")
        except OSError:
            return 0

    # ====================================================== RECORD view
    def _view_record(self):
        wrap = tk.Frame(self.content, bg=DARK_BG)
        wrap.pack(fill="both", expand=True)
        left = tk.Frame(wrap, bg=DARK_BG, width=px(440))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        p = W.CutPanel(left, padx=12, grow=True)
        p.pack(fill="both", expand=True)
        self.games_right = W.section_title(p.inner, "GAMES", EDGE_HI,
                                           right="")
        cols = [W.Col("icon", "", 0, kind="icon"),
                W.Col("champ", "CHAMPION", 8, "w", stretch=True),
                W.Col("vs", "VS", 7, "w"),
                W.Col("res", "", 1),
                W.Col("date", "DATE", 7, "e"),
                W.Col("clips", "CLIPS", 5, "center"),
                W.Col("rec", "REPLAY", 8, "center")]
        self.games_tbl = W.Table(p.inner, cols, icon_loader=self.icons.get,
                                 on_click=lambda r: self.open_game(
                                     r.data.match_id),
                                 rows_visible=None, tooltip=self.tooltip)
        self.games_tbl.pack(fill="both", expand=True)
        self.right = W.ScrollArea(wrap)
        self.right.pack(side="left", fill="both", expand=True,
                        padx=(SP_L, 0))
        self._fill_games()
        self._render_game()

    def _fill_games(self):
        if not getattr(self, "games_tbl", None) or not \
                self.games_tbl.winfo_exists():
            return
        rows = []
        for g in self.games:
            n = self._clip_count(g)
            rows.append(W.Row({
                "champ": (g.champion, FG),
                "vs": (g.opponent or "", DIM),
                "res": ("W" if g.win else "L", GOOD if g.win else BAD),
                "date": (_date(g.created), DIM),
                "clips": (str(n) if n else "—", GOOD if n else DIM),
                "rec": ("old patch", mix(DIM, PANEL, 0.3))
                if self.old_patch(g.version) else ("● ready", GOOD)},
                icon=g.champion, data=g,
                marked=bool(self.game and self.game.match_id == g.match_id)))
        self.games_tbl.set_rows(rows)
        if self.games_right is not None:
            self.games_right.configure(text="%d ranked · newest first"
                                       % len(rows))

    # ----------------------------------------------------- open a game
    def open_game(self, match_id: str, keep_sel=False):
        if not self.player:
            return
        if self.rec is not None:
            self.set_status("Recording — cancel it first to switch games",
                            EDGE_HI)
            return
        puuid = self.player["puuid"]

        def work():
            m = self.cache.get_match(match_id)
            tl = self.cache.get_timeline(match_id)
            try:
                g = build_game(m, tl, puuid)
                hs = score.find_highlights(g, static.champion_name)
            except Exception as e:
                self.post(self.set_status, "Can't open %s: %s" % (
                    match_id, e), BAD)
                return
            self.post(done, g, m, hs)

        def done(g, m, hs):
            same = self.game is not None and self.game.match_id == g.match_id
            self.game, self.match, self.hl = g, m, hs
            if not (keep_sel and same):
                self.sel = score.default_selection(hs)
            self.results = []
            self.replay_state = {}
            if self.view != "record":
                self.navigate("record")
            else:
                self._fill_games()
                self._render_game()
            self._check_replay()
        threading.Thread(target=work, daemon=True).start()

    def _render_game(self):
        if not getattr(self, "right", None) or not self.right.winfo_exists():
            return
        body = self.right.body
        _clear(body)
        g = self.game
        if g is None:
            p, _ = panel(body, "GAME")
            tk.Label(p, text="Pick a game on the left. New games: "
                     "“Fetch new games” (uses Review's key and cache).",
                     bg=PANEL, fg=DIM, font=F_SM).pack(anchor="w")
            return
        self._game_header(body, g)
        self._highlights_panel(body, g)
        self._recorded_panel(body, g)

    def _game_header(self, body, g: GameView):
        panel_inner, _ = panel(body, "GAME", right="%s · patch %s · %s" % (
            QUEUE_SHORT.get(g.queue, g.queue), g.patch, _date(g.created_ms)))
        top = tk.Frame(panel_inner, bg=PANEL)
        top.pack(fill="x")
        ic = self.icons.get(g.me.champion, px(44),
                            lambda: self._render_game())
        if ic is not None:
            tk.Label(top, image=ic, bg=PANEL).pack(side="left",
                                                   padx=(0, SP_L))
        t = tk.Frame(top, bg=PANEL)
        t.pack(side="left")
        name = static.champion_name(g.me.champion)
        vs = static.champion_name(g.opponent.champion) if g.opponent else "?"
        tk.Label(t, text="%s  vs  %s" % (name, vs), bg=PANEL, fg=FG,
                 font=F_LG_B).pack(anchor="w")
        sub = "%s · %s · %s · %d CS · %s" % (
            "Victory" if g.win else "Defeat",
            ROLE_SHORT.get(g.me.role or "", "?"), g.kda_text, g.cs,
            fmt_time(g.duration_s))
        tk.Label(t, text=sub, bg=PANEL, fg=GOOD if g.win else BAD,
                 font=F_SM).pack(anchor="w")
        self.state_box = tk.Frame(top, bg=PANEL)
        self.state_box.pack(side="right", anchor="n")
        self._render_state()

    def _can_record(self) -> tuple[bool, str, str]:
        """(ok, text, colour) for the open game."""
        g = self.game
        if g is None:
            return False, "", DIM
        if self.demo:
            return True, "Demo: recording is simulated", EDGE_HI
        if self.old_patch(g.version):
            return False, ("Can't record: this game is on patch %s and "
                           "Riot only keeps replays for the current patch "
                           "(%s)" % (g.patch, self.current_patch)), BAD
        st = self.replay_state.get("state")
        if st in recorder.CANT_RECORD:
            return False, "Can't record: %s" % self.replay_state.get(
                "text", st), BAD
        if st == "offline":
            return True, "Start the League client to record", EDGE_HI
        if st == "watch":
            return True, "Replay ready to record", GOOD
        return True, self.replay_state.get("text", "Checking replay…"), DIM

    def _render_state(self):
        box = getattr(self, "state_box", None)
        if box is None or not box.winfo_exists():
            return
        _clear(box)
        ok, text, col = self._can_record()
        tk.Label(box, text=text, bg=PANEL, fg=col, font=F_SM,
                 wraplength=px(330), justify="right").pack(anchor="e")

    def _check_replay(self):
        g = self.game
        if g is None or self.demo:
            return
        gid, match = g.game_id, self.match
        champs = {p.champion for p in g.players.values()}

        def work():
            try:
                md = self.lcu.metadata(gid)
                if not md:
                    try:
                        self.lcu.create_metadata(gid, match)
                        md = self.lcu.metadata(gid)
                    except local.LocalError:
                        pass
                st = md.get("state")
                res = {"state": st, "text": local.REPLAY_STATE.get(
                    st, st or "Unknown")}
                if st == "downloading" and md.get("downloadProgress"):
                    res["text"] = "Downloading… %d%%" % md["downloadProgress"]
                res["open"] = recorder._replay_is(self.rapi, champs)
            except local.NotRunning:
                res = {"state": "offline", "text": ""}
            except local.LocalError as e:
                res = {"state": "clienterror", "text": "Client: %s" % e}
            self.post(done, res)

        def done(res):
            if self.game is None or self.game.game_id != gid:
                return
            self.replay_state = res
            self._render_state()
            self._render_actions()
            if res.get("state") in ("downloading", "checking", "found"):
                self.root.after(1500, self._check_replay)
        threading.Thread(target=work, daemon=True).start()

    def _poll_local(self):
        """Every 2 s: is the client up (banner + replay state)."""
        def work():
            ok = self.lcu.running()
            self.post(done, ok)

        def done(ok):
            was = self.client_ok
            self.client_ok = ok
            self._banner()
            if ok != was and self.game is not None and self.rec is None:
                self._check_replay()
            if self._alive:
                self.root.after(2000, self._poll_local)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------ highlights
    def _order(self) -> list[int]:
        idx = list(range(len(self.hl)))
        if self.prefs.get("order", "best") == "best":
            idx.sort(key=lambda i: (-self.hl[i].score, self.hl[i].start_ms))
        return idx

    def _highlights_panel(self, body, g: GameView):
        p, self.hl_right = panel(body, "HIGHLIGHTS", pady=(SP_M, 0),
                                 right="")
        if not g.has_timeline:
            tk.Label(p, text="Riot has no timeline for this game, so there "
                     "are no moments to find.", bg=PANEL, fg=DIM,
                     font=F_SM).pack(anchor="w")
            self.hl_rows = None
            return
        if not self.hl:
            tk.Label(p, text="No kills, assists or objectives of yours in "
                     "this game.", bg=PANEL, fg=DIM, font=F_SM).pack(
                anchor="w")
            self.hl_rows = None
            return
        bar = tk.Frame(p, bg=PANEL)
        bar.pack(fill="x", pady=(0, SP_S))
        W.TabRow(bar, "ORDER", ORDERS, self.prefs.get("order", "best"),
                 on_change=self._set_order, style="teal", bg=PANEL,
                 small=True).pack(side="left")
        for text, fn in (("None", lambda: self._select(set())),
                         ("All", lambda: self._select(set(range(len(
                             self.hl))))),
                         ("Best", lambda: self._select(
                             score.default_selection(self.hl)))):
            b = W.button(bar, text, fn, fg=DIM, font=F_SM)
            b.configure(pady=3)
            b.pack(side="right", padx=(px(3), 0))
        tk.Label(bar, text="TICK", bg=PANEL, fg=DIM, font=F_XS_B).pack(
            side="right", padx=(0, SP_S))
        self.hl_rows = tk.Frame(p, bg=PANEL)
        self.hl_rows.pack(fill="x")
        self._render_rows()
        self.actions = tk.Frame(p, bg=PANEL)
        self.actions.pack(fill="x", pady=(SP_M, 0))
        self._render_actions()

    def _set_order(self, v):
        self.prefs.set("order", v)
        self._render_rows()

    def _select(self, s: set[int]):
        if self.rec is not None:
            return
        self.sel = set(s)
        self._render_rows()
        self._render_actions()

    def toggle(self, i: int):
        if self.rec is not None:
            return
        self.sel ^= {i}
        self._render_rows()
        self._render_actions()

    def _render_rows(self):
        box = getattr(self, "hl_rows", None)
        if box is None or not box.winfo_exists():
            return
        _clear(box)
        top = max((h.score for h in self.hl), default=1)
        scale = max(top, 60)
        hdr = tk.Frame(box, bg=PANEL)
        hdr.pack(fill="x", pady=(0, SP_XS))
        for text, w, side in (("", 3, "left"), ("TIME", 6, "left"),
                              ("SCORE", 11, "left"), ("MOMENT", 0, "left"),
                              ("LENGTH", 7, "right")):
            lb = tk.Label(hdr, text=text, bg=PANEL, fg=DIM, font=F_XS_B,
                          anchor="w" if side == "left" else "e")
            if w:
                lb.configure(width=w)
            lb.pack(side=side)
        recording = self.rec is not None
        for i in self._order():
            h = self.hl[i]
            on = i in self.sel
            bg = HOVER if on else PANEL
            r = tk.Frame(box, bg=bg, cursor="" if recording else "hand2")
            r.pack(fill="x", pady=1)
            tk.Frame(r, bg=EDGE_HI if on else bg, width=px(3)).pack(
                side="left", fill="y")
            tk.Label(r, text="☑" if on else "☐", bg=bg, fg=GOOD if on
                     else DIM, font=F_SM_B, width=2).pack(side="left",
                                                          padx=(px(4), 0))
            tk.Label(r, text=fmt_time(h.t_ms / 1000), bg=bg, fg=FG,
                     font=F_SM, width=6, anchor="w").pack(side="left")
            sc = tk.Frame(r, bg=bg)
            sc.pack(side="left", padx=(0, SP_M))
            cv = tk.Canvas(sc, bg=bg, highlightthickness=0, width=px(52),
                           height=px(8))
            cv.pack(side="left", pady=px(6))
            f = min(h.score / scale, 1.0)
            cv.create_rectangle(0, 0, px(52), px(8), fill=PANEL2, width=0)
            cv.create_rectangle(0, 0, px(52) * f, px(8), width=0,
                                fill=ACCENT if h.score >= 30 else mix(
                                    ACCENT, PANEL, 0.4))
            tk.Label(sc, text=str(h.score), bg=bg, fg=EDGE_HI if
                     h.score >= 30 else DIM, font=F_XS_B, width=3,
                     anchor="e").pack(side="left")
            tk.Label(r, text=_dur(h.length_s), bg=bg, fg=DIM, font=F_SM,
                     width=7, anchor="e").pack(side="right",
                                               padx=(0, px(6)))
            txt = tk.Frame(r, bg=bg)
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=h.title, bg=bg, fg=FG if on else
                     mix(FG, DIM, 0.5), font=F_SM_B if on else F_SM,
                     anchor="w").pack(anchor="w")
            if len(h.beats) > 1 or h.beats[0].label != h.title:
                tk.Label(txt, text=h.details, bg=bg, fg=DIM, font=F_XS,
                         anchor="w", justify="left",
                         wraplength=px(560)).pack(anchor="w")
            self.tooltip.bind(r, "%s – %s in the replay" % (
                fmt_time(h.start_ms / 1000), fmt_time(h.end_ms / 1000)))
            for w in (r, *r.winfo_children(), *txt.winfo_children(),
                      *sc.winfo_children()):
                w.bind("<Button-1>", lambda e, i=i: self.toggle(i))
        if self.hl_right is not None:
            self.hl_right.configure(text="%d found · %d ticked" % (
                len(self.hl), len(self.sel)))

    def selected(self) -> list[int]:
        """Ticked clips in game order (the order they're recorded)."""
        return sorted(self.sel, key=lambda i: self.hl[i].start_ms)

    def estimate(self) -> int:
        open_ = self.replay_state.get("open", False)
        return score.estimate_s([self.hl[i] for i in self.selected()],
                                replay_open=open_)

    def _render_actions(self):
        box = getattr(self, "actions", None)
        if box is None or not box.winfo_exists():
            return
        _clear(box)
        self.rec_bar = self.rec_lbl = None
        if self.rec is not None:
            line = tk.Frame(box, bg=PANEL)
            line.pack(fill="x")
            self.rec_lbl = tk.Label(line, text="", bg=PANEL, fg=FG,
                                    font=F_SM, anchor="w")
            self.rec_lbl.pack(side="left", fill="x", expand=True)
            W.button(line, "Cancel", self.cancel_recording, fg=BAD,
                     font=F_SM).pack(side="right")
            self.rec_bar = W.ProgressBar(box, height=6)
            self.rec_bar.pack(fill="x", pady=(SP_S, 0))
            self.rec_bar.start()
            self._update_progress()
            return
        sel = self.selected()
        line = tk.Frame(box, bg=PANEL)
        line.pack(fill="x")
        ok, _, _ = self._can_record()
        video = sum(self.hl[i].length_s for i in sel)
        if sel:
            est = "%d clip%s · %s of video · about %s to record (real time)" \
                % (len(sel), "" if len(sel) == 1 else "s", _dur(video),
                   _dur(self.estimate()))
        else:
            est = "Tick the moments you want as videos"
        tk.Label(line, text=est, bg=PANEL, fg=FG if sel else DIM,
                 font=F_SM).pack(side="left")
        if ok and sel:
            W.button(line, "●  Record selected", self.record,
                     gold=True).pack(side="right")
        else:
            tk.Label(line, text="●  Record selected", bg=PANEL2,
                     fg=mix(DIM, PANEL, 0.4), font=F_SM_B, padx=14,
                     pady=6).pack(side="right")
        tk.Label(box, text="League comes to the front while it records "
                 "(video size = its window) · saved to %s" % (
                     self.out_dir() / score.folder_name(self.game)),
                 bg=PANEL, fg=DIM, font=F_XS).pack(anchor="e",
                                                   pady=(SP_XS, 0))

    # ------------------------------------------------------- recording
    def record(self):
        g = self.game
        if g is None or self.rec is not None:
            return
        idx = self.selected()
        if not idx:
            self.set_status("Tick at least one moment", EDGE_HI)
            return
        ok, why, _ = self._can_record()
        if not ok:
            self.set_status(why, BAD)
            return
        names = score.file_names(self.hl)
        folder = self.out_dir() / score.folder_name(g)
        clips = [Clip(self.hl[i].start_ms / 1000, self.hl[i].end_ms / 1000,
                      folder / names[i], self.hl[i].title) for i in idx]
        est = self.estimate()
        self.cancel = cancel = threading.Event()
        self.rec = {"clips": clips, "p": Progress("loading", 0, len(clips)),
                    "t0": time.monotonic(), "est": est, "msg": ""}
        self.results = []
        self.set_status("Recording %d clips · about %s" % (len(clips),
                                                          _dur(est)))
        self._render_rows()
        self._render_actions()
        champs = {p.champion for p in g.players.values()}
        me = (g.me.champion, g.me.riot_id.split("#")[0])
        prog = lambda p: self.post(self._on_progress, p)    # noqa: E731

        def work():
            err = None
            res: list[ClipResult] = []
            try:
                if self.demo:
                    res = _simulate(clips, prog, cancel)
                else:
                    recorder.open_replay(
                        self.lcu, self.rapi, g.game_id, self.match, champs,
                        cancel, status=lambda m: self.post(self._on_load, m))
                    rec = recorder.Recorder(self.rapi, *me,
                                            on_progress=prog, cancel=cancel)
                    res = rec.record_all(clips)
            except recorder.Cancelled:
                err = "Cancelled"
            except recorder.NotRecordable as e:
                err = ("Can't record this game: %s. Riot keeps replays only "
                       "for the current patch." % e)
            except local.NotRunning:
                err = "Start the League client first, then press Record again"
            except (local.LocalError, TimeoutError, OSError) as e:
                err = str(e)
            except Exception as e:                  # never leave it hanging
                self._log(traceback.format_exc())
                err = "%s: %s" % (e.__class__.__name__, e)
            self.post(self._finished, res, err, cancel.is_set())
        threading.Thread(target=work, daemon=True).start()

    def cancel_recording(self):
        if self.cancel is not None:
            self.cancel.set()
            self.set_status("Cancelling…", EDGE_HI)

    def _on_load(self, msg):
        if self.rec is not None:
            self.rec["msg"] = msg
            self._update_progress()

    def _on_progress(self, p: Progress):
        if self.rec is None:
            return
        self.rec["p"] = p
        if p.phase == "error":
            self.rec["msg"] = p.message
        self._update_progress()

    def _remaining(self) -> int:
        r = self.rec
        p: Progress = r["p"]
        clips = r["clips"]
        if p.phase == "loading":
            return r["est"]
        left = sum(c.end_s - c.start_s for c in clips[p.index + 1:])
        if p.index < len(clips):
            c = clips[p.index]
            left += (1 - p.frac) * (c.end_s - c.start_s)
        return int(left + score.SETUP_S * max(len(clips) - p.index -
                                              (1 if p.frac else 0), 0))

    def _update_progress(self):
        r = self.rec
        lbl, bar = getattr(self, "rec_lbl", None), getattr(self, "rec_bar",
                                                            None)
        if r is None or lbl is None or not lbl.winfo_exists():
            return
        p: Progress = r["p"]
        n = len(r["clips"])
        if p.phase == "loading":
            text = r["msg"] or "Opening the replay…"
            frac = 0.0
        else:
            c = r["clips"][min(p.index, n - 1)]
            verb = {"seeking": "Jumping to", "camera": "Camera on you for",
                    "window": "Click League's replay window — waiting to "
                    "record",
                    "recording": "● Recording", "saving": "Saving",
                    "error": "Failed:", "done": "Done",
                    "cancelled": "Cancelled"}.get(p.phase, p.phase)
            text = "Clip %d/%d · %s %s" % (min(p.index + 1, n), n, verb,
                                          c.title)
            frac = (p.index + p.frac) / n
        text += "  ·  about %s left" % _dur(self._remaining())
        lbl.configure(text=text, fg=BAD if p.phase == "error" else FG)
        if bar is not None:
            bar.set(frac)

    def _finished(self, results: list[ClipResult], err: str | None,
                  cancelled: bool):
        if not self.demo:                # League was in front while recording
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except tk.TclError:
                pass
        self.rec = None
        self.cancel = None
        self.results = results
        ok = [r for r in results if r.ok]
        bad = [r for r in results if not r.ok]
        if self.demo and not err:
            self.set_status("Demo: %d clips would have been recorded — "
                            "nothing was saved" % len(ok), EDGE_HI)
        elif err and not ok:
            self.set_status(err, EDGE_HI if cancelled else BAD)
        elif bad:
            self.set_status("Recorded %d of %d clips · %s" % (
                len(ok), len(results), bad[0].error), BAD)
        else:
            self.set_status("Recorded %d clip%s%s" % (
                len(ok), "" if len(ok) == 1 else "s",
                " (then cancelled)" if cancelled else ""), GOOD)
        if self.view == "record":
            self._render_rows()
            self._render_actions()
            self._refresh_recorded()
            self._fill_games()

    # ------------------------------------------------- recorded clips
    def _recorded_panel(self, body, g: GameView):
        p, self.done_right = panel(body, "RECORDED", GOOD, pady=(SP_M, 0),
                                   right="")
        self.done_box = p
        self._refresh_recorded()

    def _refresh_recorded(self):
        box = getattr(self, "done_box", None)
        if box is None or not box.winfo_exists() or self.game is None:
            return
        for w in box.winfo_children()[2:]:      # keep the section title
            w.destroy()
        folder = self.out_dir() / score.folder_name(self.game)
        files = _videos(folder)
        if self.done_right is not None:
            self.done_right.configure(text="%d clip%s · click to play" % (
                len(files), "" if len(files) == 1 else "s") if files
                else "")
        if not files:
            tk.Label(box, text="Nothing recorded for this game yet. Clips "
                     "go to %s" % folder, bg=PANEL, fg=DIM, font=F_SM,
                     wraplength=px(700), justify="left").pack(anchor="w")
        for f in files:
            self._clip_row(box, f)
        acts = tk.Frame(box, bg=PANEL)
        acts.pack(fill="x", pady=(SP_M, 0))
        W.button(acts, "Open folder", lambda: self.open_folder(folder),
                 font=F_SM).pack(side="right")

    def _clip_row(self, box, f: Path, show_game=False):
        try:
            st = f.stat()
        except OSError:
            return
        r = tk.Frame(box, bg=PANEL, cursor="hand2")
        r.pack(fill="x", pady=1)
        tk.Label(r, text="▶", bg=PANEL, fg=GOOD, font=F_SM_B, width=2).pack(
            side="left", padx=(px(4), 0))
        tk.Label(r, text=f.name, bg=PANEL, fg=FG, font=F_SM,
                 anchor="w").pack(side="left")
        if show_game:
            tk.Label(r, text="  " + f.parent.name.replace("_", " "),
                     bg=PANEL, fg=DIM, font=F_XS).pack(side="left")
        tk.Label(r, text=datetime.fromtimestamp(st.st_mtime).strftime(
            "%d %b %H:%M"), bg=PANEL, fg=DIM, font=F_XS, width=13,
            anchor="e").pack(side="right", padx=(0, px(6)))
        tk.Label(r, text=_mb(st.st_size), bg=PANEL, fg=DIM, font=F_XS,
                 width=9, anchor="e").pack(side="right")

        def enter(e, r=r):
            for w in (r, *r.winfo_children()):
                w.configure(bg=HOVER)

        def leave(e, r=r):
            for w in (r, *r.winfo_children()):
                w.configure(bg=PANEL)
        r.bind("<Enter>", enter)
        r.bind("<Leave>", leave)
        for w in (r, *r.winfo_children()):
            w.bind("<Button-1>", lambda e, f=f: self.play(f))

    def play(self, f: Path):
        try:
            open_path(f)
            self.set_status("Playing %s" % f.name)
        except OSError as e:
            self.set_status("Can't open %s: %s" % (f.name, e), BAD)

    def open_folder(self, folder: Path):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            open_path(folder)
        except OSError as e:
            self.set_status("Can't open %s: %s" % (folder, e), BAD)

    # ======================================================= CLIPS view
    def _view_clips(self):
        sa = W.ScrollArea(self.content)
        sa.pack(fill="both", expand=True)
        body = sa.body
        root = self.out_dir()
        games = []
        try:
            for d in root.iterdir():
                if d.is_dir():
                    fs = _videos(d)
                    if fs:
                        games.append((max(f.stat().st_mtime for f in fs), d,
                                      fs))
        except OSError:
            pass
        games.sort(key=lambda t: -t[0])
        p, _ = panel(body, "YOUR CLIPS", GOOD, right="%d clips · %d games · "
                     "click to play" % (sum(len(t[2]) for t in games),
                                        len(games)))
        top = tk.Frame(p, bg=PANEL)
        top.pack(fill="x")
        tk.Label(top, text=str(root), bg=PANEL, fg=DIM, font=F_SM).pack(
            side="left")
        W.button(top, "Open folder", lambda: self.open_folder(root),
                 font=F_SM).pack(side="right")
        if not games:
            tk.Label(p, text="No clips yet. Pick a game under Record, tick "
                     "the moments you want and press Record selected.",
                     bg=PANEL, fg=DIM, font=F_SM).pack(anchor="w",
                                                       pady=(SP_M, 0))
        for _, d, fs in games:
            W.caption(p, d.name.replace("_", "  ").upper())
            for f in fs:
                self._clip_row(p, f)

    # ------------------------------------------------------ settings
    def show_settings(self):
        d = tk.Toplevel(self.root, bg=BORDER)
        d.title("Highlights settings")
        d.transient(self.root)
        body = tk.Frame(d, bg=PANEL, padx=px(16), pady=px(12))
        body.pack(padx=1, pady=1)
        W.section_title(body, "SETTINGS", EDGE_HI)
        W.caption(body, "VIDEO", pady=(0, SP_XS))
        tk.Label(body, text="League records its own window: the video is "
                 "as big as League's window (Video settings in the game), "
                 "in real time, as smooth as your PC renders while "
                 "encoding. Highlights brings the replay to the front for "
                 "each clip — leave it there until the clip is done.",
                 bg=PANEL, fg=FG, font=F_SM, wraplength=px(380),
                 justify="left").pack(anchor="w")
        fields = {}

        def field(key, label, default, hint, browse=False):
            W.caption(body, label)
            row = tk.Frame(body, bg=PANEL)
            row.pack(anchor="w")
            e = W.entry(row, 40)
            e.insert(0, str(self.prefs.get(key) or default))
            e.pack(side="left")
            if browse:
                def pick(e=e):
                    from tkinter import filedialog
                    p = filedialog.askdirectory(parent=d,
                                                initialdir=e.get())
                    if p:
                        e.delete(0, "end")
                        e.insert(0, os.path.normpath(p))
                W.button(row, "Browse…", pick, font=F_SM, fg=DIM).pack(
                    side="left", padx=(SP_S, 0))
            tk.Label(body, text=hint, bg=PANEL, fg=DIM, font=F_XS,
                     wraplength=px(380), justify="left").pack(anchor="w")
            fields[key] = e
        field("out_dir", "SAVE CLIPS TO", recorder.default_out_dir(),
              "One folder per game: <date>_<champion>_vs_<opponent>.",
              browse=True)
        field("league_dir", "LEAGUE FOLDER", str(self.install),
              "Where League is installed (has Config\\game.cfg and the "
              "client's lockfile).")

        def save():
            for k, e in fields.items():
                self.prefs.set(k, e.get().strip())
            self.install = local.install_dir(self.prefs.get("league_dir"))
            self.lcu = local.LeagueClient(self.install)
            d.destroy()
            if self.view == "record":
                self._fill_games()
                self._render_game()
            else:
                self.navigate(self.view)
        W.button(body, "Save", save, gold=True).pack(anchor="e",
                                                    pady=(SP_L, 0))
        tk.Label(body, text=LEGAL, bg=PANEL, fg=mix(DIM, PANEL, 0.3),
                 font=F_XS, wraplength=px(380), justify="left").pack(
            anchor="w", pady=(SP_L, 0))

    # ------------------------------------------------------ lifecycle
    def _log(self, msg):
        try:
            with open(config.data_dir() / "highlights.log", "a",
                      encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.ctime(), msg))
        except OSError:
            pass

    def close(self):
        try:
            if self.cancel is not None:
                self.cancel.set()      # the worker stops the recorder
            g = self.root.geometry()
            if window.remember(g):              # never off-screen
                self.prefs.set("geometry", g)
            if self.job:
                self.job.cancel()
        except Exception as e:
            self._log(e)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def _videos(folder: Path) -> list[Path]:
    try:
        fs = [f for f in folder.iterdir()
              if f.suffix.lower() in (".webm", ".mp4") and f.is_file()]
    except OSError:
        return []
    return sorted(fs, key=lambda f: f.name)


def _simulate(clips: list[Clip], prog, cancel: threading.Event,
              step: float = 0.05, per_clip: float = 1.5) -> list[ClipResult]:
    """--demo: the recording flow without a game (nothing is written)."""
    out = []
    n = len(clips)
    for i, c in enumerate(clips):
        for phase in ("seeking", "camera"):
            prog(Progress(phase, i, n, 0, c.title))
            if cancel.wait(step * 4):
                raise recorder.Cancelled()
        k = max(int(per_clip / step), 1)
        for j in range(k):
            prog(Progress("recording", i, n, (j + 1) / k, c.title))
            if cancel.wait(step):
                raise recorder.Cancelled()
        out.append(ClipResult(c, True, None))
    prog(Progress("done", n, n, 1.0))
    return out
