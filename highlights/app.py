"""HIGHLIGHTS desktop app — DRAFT's window, a small editor for your replays.

Works like an editing program: you watch the replay in League (borderless
window above or beside this one), and here you get

* the remote: ⟲10 · play/pause · 10⟳ · speed · Follow me (camera on you),
* a timeline of the whole game — kills, highlight blocks (length = clip,
  brightness = score), objectives — with a playhead that follows the
  replay; click a block to jump there, drag its edges to trim it, or set
  in/out at the playhead with I / O,
* the selected clip: in, out, length, Preview and Save clip (League
  replays just that part once and records it),
* the clips you've saved for this game; click to play.

The Clips view lists every clip you've saved. Matches and timelines come
from Review's cache, like REPLAY; recording goes through League's own
replay recorder (``recorder.py``). All network and disk work runs off the
UI thread and comes back through ``post``.
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
from replay.app import result_cell, result_text
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
from review.theme.tokens import (BAD, BORDER, DARK_BG, DIM, EDGE_HI, F_LG_B,
                                 F_MD_B, F_SM, F_SM_B, F_XS, F_XS_B, FG, GOOD,
                                 HOVER, PANEL, PANEL2, SP_L, SP_M, SP_S, SP_XS,
                                 TEAL_BG, mix)
from review.theme.widgets import px
from review.ui.assets import IconStore

from . import recorder, score
from .recorder import Clip, ClipResult, Progress
from .timeline import MIN_CLIP_MS, ClipTimeline, Span

VIEWS = [("editor", "Editor"), ("clips", "Clips")]
SPEEDS = [(0.5, "0.5×"), (1.0, "1×"), (2.0, "2×")]
KEYS = ("Space play/pause · ←/→ 10 s · N/P next/previous clip · I/O in/out "
        "at the playhead · Enter preview · S save · F follow me")
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
    # REPLAY's account picker, game fetch, UI-thread queue and "bring the
    # camera back when it wanders off", as they are
    ask_account = _Replay.ask_account
    switch = _Replay.switch
    fetch = _Replay.fetch
    _progress = _Replay._progress
    _known_players = _Replay._known_players
    post = _Replay.post
    _drain = _Replay._drain
    _keep_on_me = _Replay._keep_on_me

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
        self.names: list[str] = []
        self.trims: dict[int, tuple[int, int]] = {}
        self.sel_i: int | None = None
        self._champs: set[str] = set()
        self.view = "editor"
        self.client_ok = False
        self.replay_state: dict = {}
        self.playback: dict | None = None      # last /replay/playback
        self.this_open = False                 # the open replay is this game
        self.opening = False
        self.preview_out: float | None = None  # pause the preview here (ms)
        self.job = None                        # Review's SyncJob (Fetch)
        self.rec: dict | None = None           # the clip being saved
        self.cancel: threading.Event | None = None
        self.results: list[ClipResult] = []
        self.tl: ClipTimeline | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._alive = True
        self._offscreen = 0
        self._polls = 0
        self._build()
        self.root.after(30, self._drain)
        root.geometry(window.safe_geometry(
            root, self.prefs.get("geometry"), (1320, 620)))
        root.minsize(px(980), px(520))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Destroy>", lambda e: e.widget is root and setattr(
            self, "_alive", False), add="+")
        root.report_callback_exception = lambda exc, val, tb: self._log(
            "".join(traceback.format_exception(exc, val, tb)))
        self._bind_keys()
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
        self.nav = W.TabRow(nav, "VIEW", VIEWS, "editor",
                            on_change=self.navigate)
        self.nav.pack(side="left")
        for text, cmd, pad in (("Fetch new games", self.fetch, 0),
                               ("Account", self.ask_account, SP_S),
                               ("Games…", self.pick_game, SP_S)):
            b = W.button(nav, text, cmd, fg=DIM, font=F_SM)
            b.configure(pady=4)     # like the tabs (DRAFT's row height)
            b.pack(side="right", padx=(0, pad))
        self.content = tk.Frame(outer, bg=DARK_BG)
        self.content.pack(fill="both", expand=True)
        bottom = tk.Frame(outer, bg=DARK_BG)
        bottom.pack(fill="x", side="bottom", pady=(SP_M, 0))
        tk.Label(bottom, text=KEYS, bg=DARK_BG, fg=mix(DIM, DARK_BG, 0.35),
                 font=F_XS).pack(side="left", anchor="s")
        self.status = tk.Label(bottom, text="", bg=DARK_BG, fg=DIM, font=F_SM)
        self.status.pack(side="right", anchor="s")
        self.navigate("editor")

    def set_status(self, text, color=DIM):
        self.status.configure(text=text, fg=color)

    def _banner(self):
        bits = []
        if self.player:
            bits.append("%s · %s" % (self.player["riot_id"],
                                     self.player["region"]))
        if self.demo:
            bits.append("demo — no League, nothing is recorded")
        else:
            bits.append("client " + ("connected" if self.client_ok
                                     else "not running"))
            if self.playback:
                bits.append("replay %s%s" % (fmt_time(self.playback.get(
                    "time", 0)), " ❚❚" if self.playback.get("paused")
                    else " ▶"))
        self.banner.set_context(" · ".join(bits))

    def navigate(self, name):
        self.view = name
        self.nav.select(name, fire=False)
        _clear(self.content)
        self.tl = None
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
                self._render_remote()
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
            self.set_status("%d games · Games… to switch" % len(rows))
            if then:
                then()
            elif self.game is None and rows:
                self.open_game(rows[0].match_id)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------- recordable or not
    def old_patch(self, version: str) -> bool:
        """Riot keeps replays for the current patch only."""
        if self.demo or not self.current_patch:
            return False
        return ".".join(version.split(".")[:2]) != self.current_patch

    def _clip_count(self, g: GameRow) -> int:
        return len(_videos(self.out_dir() / score.folder_for(
            g.created, g.champion, g.opponent)))

    # ------------------------------------------------------ pick a game
    def pick_game(self):
        if self.rec is not None:
            self.set_status("Saving a clip — cancel it first to switch games",
                            EDGE_HI)
            return
        d = tk.Toplevel(self.root, bg=BORDER)
        d.title("Games")
        d.transient(self.root)
        body = tk.Frame(d, bg=PANEL, padx=px(14), pady=px(10))
        body.pack(padx=1, pady=1, fill="both", expand=True)
        W.section_title(body, "GAMES", EDGE_HI, right="%d ranked · newest "
                        "first · replays only for the current patch"
                        % len(self.games))
        cols = [W.Col("icon", "", 0, kind="icon"),
                W.Col("champ", "CHAMPION", 9, "w", stretch=True),
                W.Col("vs", "VS", 9, "w"),
                W.Col("res", "", 2),
                W.Col("kda", "KDA", 9, "center"),
                W.Col("date", "DATE", 7, "e"),
                W.Col("clips", "CLIPS", 5, "center"),
                W.Col("rec", "REPLAY", 9, "center")]

        def go(r):
            d.destroy()
            self.open_game(r.data.match_id)
        t = W.Table(body, cols, icon_loader=self.icons.get, on_click=go,
                    rows_visible=12, tooltip=self.tooltip)
        t.pack(fill="both", expand=True)
        rows = []
        for g in self.games:
            n = self._clip_count(g)
            rows.append(W.Row({
                "champ": (g.champion, FG), "vs": (g.opponent or "", DIM),
                "res": result_cell(g),
                "kda": (g.kda, DIM), "date": (_date(g.created), DIM),
                "clips": (str(n) if n else "—", GOOD if n else DIM),
                "rec": ("old patch", mix(DIM, PANEL, 0.3))
                if self.old_patch(g.version) else ("● ready", GOOD)},
                icon=g.champion, data=g,
                marked=bool(self.game and self.game.match_id == g.match_id)))
        t.set_rows(rows)
        d.geometry("%dx%d" % (px(760), px(470)))
        self._games_dialog = d
        self._games_table = t

    def open_game(self, match_id: str, keep_sel=False):
        if not self.player:
            return
        if self.rec is not None:
            self.set_status("Saving a clip — cancel it first to switch games",
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
            self.names = score.file_names(hs)
            if not (keep_sel and same):
                self.trims = {}
                self.sel_i = max(range(len(hs)), key=lambda i: hs[i].score) \
                    if hs else None
            self.results = []
            self.replay_state = {}
            self.this_open = False
            self.preview_out = None
            self._champs = {p.champion for p in g.players.values()}
            self.navigate("editor")
            self._check_replay()
            self._check_open()
        threading.Thread(target=work, daemon=True).start()

    # ====================================================== EDITOR view
    def _view_editor(self):
        body = tk.Frame(self.content, bg=DARK_BG)
        body.pack(fill="both", expand=True)
        g = self.game
        if g is None:
            p, _ = panel(body, "GAME")
            tk.Label(p, text="No game yet. “Fetch new games” brings your "
                     "latest ranked games (uses Review's key and cache); "
                     "“Games…” picks one.", bg=PANEL, fg=DIM,
                     font=F_SM).pack(anchor="w")
            return
        self._header(body, g)
        p, _ = panel(body, "TIMELINE", pady=(SP_M, 0), right=(
            "click a block to jump there · drag its edges to trim · click "
            "anywhere else to seek" if g.has_timeline else
            "Riot has no timeline for this game"))
        self.tl = ClipTimeline(p, self.tooltip, on_pick=self.pick,
                               on_seek=self.seek_to, on_trim=self.trim)
        self.tl.pack(fill="x")
        self.tl.locked = self.rec is not None
        self.tl.set(g.duration_s * 1000, g.moments, self._spans(),
                    self.sel_i)
        if self.playback is not None and self.this_open:
            self.tl.set_playhead(self.playback.get("time", 0) * 1000)
        row = tk.Frame(body, bg=DARK_BG)
        row.pack(fill="both", expand=True, pady=(SP_M, 0))
        row.grid_columnconfigure(0, weight=3, uniform="e")
        row.grid_columnconfigure(1, weight=2, uniform="e")
        row.grid_rowconfigure(0, weight=1)
        lp = W.CutPanel(row, padx=14, grow=True)
        lp.grid(row=0, column=0, sticky="nsew", padx=(0, SP_M))
        rp = W.CutPanel(row, padx=14, grow=True)
        rp.grid(row=0, column=1, sticky="nsew")
        self.clip_box, self.saved_box = lp.inner, rp.inner
        self._render_clip()
        self._render_saved()

    def _spans(self) -> list[Span]:
        out = []
        for i, h in enumerate(self.hl):
            a, b = self.clip_range(i)
            out.append(Span(a, b, h.score, h.title,
                            saved=self.clip_path(i).exists()))
        return out

    def clip_range(self, i: int) -> tuple[int, int]:
        h = self.hl[i]
        return self.trims.get(i, (h.start_ms, h.end_ms))

    def clip_path(self, i: int) -> Path:
        return self.out_dir() / score.folder_name(self.game) / self.names[i]

    # header: the game + the replay remote
    def _header(self, body, g: GameView):
        p, _ = panel(body, "GAME", right="%s · patch %s · %s" % (
            QUEUE_SHORT.get(g.queue, g.queue), g.patch, _date(g.created_ms)))
        top = tk.Frame(p, bg=PANEL)
        top.pack(fill="x")
        ic = self.icons.get(g.me.champion, px(40),
                            lambda: self.navigate(self.view))
        if ic is not None:
            tk.Label(top, image=ic, bg=PANEL).pack(side="left",
                                                   padx=(0, SP_L))
        t = tk.Frame(top, bg=PANEL)
        t.pack(side="left")
        name = static.champion_name(g.me.champion)
        vs = static.champion_name(g.opponent.champion) if g.opponent else "?"
        tk.Label(t, text="%s  vs  %s" % (name, vs), bg=PANEL, fg=FG,
                 font=F_LG_B).pack(anchor="w")
        res, res_fg = result_text(g)
        tk.Label(t, text="%s · %s · %s · %s · %d highlights" % (
            res, ROLE_SHORT.get(g.me.role or "", "?"), g.kda_text,
            fmt_time(g.duration_s), len(self.hl)), bg=PANEL,
            fg=res_fg, font=F_SM).pack(anchor="w")
        self.remote = tk.Frame(top, bg=PANEL)
        self.remote.pack(side="right")
        self._render_remote()

    def _replay_line(self) -> tuple[str, str, str | None]:
        """(text, colour, button) for the open game's replay."""
        g = self.game
        if self.demo:
            return "Demo: no League here — jumps and saving are simulated", \
                EDGE_HI, None
        if self.old_patch(g.version):
            return ("Old patch (%s): Riot only keeps replays for the current "
                    "patch" % g.patch), BAD, None
        if self.opening:
            return self.replay_state.get("msg", "Opening the replay…"), \
                EDGE_HI, None
        if self.this_open:
            return "Replay open", GOOD, None
        st = self.replay_state.get("state")
        if st in recorder.CANT_RECORD:
            return "Can't play: %s" % self.replay_state.get("text", st), \
                BAD, None
        if st == "offline":
            return "Start the League client to watch", EDGE_HI, None
        if self.playback is not None:
            return "Another game's replay is open", EDGE_HI, "open"
        if st in ("watch", "download", "retryDownload"):
            return "", DIM, "open"
        return self.replay_state.get("text", "Checking replay…"), DIM, "open"

    def _render_remote(self):
        box = getattr(self, "remote", None)
        if box is None or not box.winfo_exists() or self.game is None:
            return
        _clear(box)
        text, col, btn = self._replay_line()
        line = tk.Frame(box, bg=PANEL)
        line.pack(anchor="e")
        if text:
            tk.Label(line, text=text, bg=PANEL, fg=col, font=F_SM).pack(
                side="left", padx=(0, SP_M))
        if btn == "open" and self.rec is None:
            W.button(line, "▶  Watch replay", self.open_replay,
                     gold=True).pack(side="left")
        ctl = tk.Frame(box, bg=PANEL)
        ctl.pack(anchor="e", pady=(SP_S, 0))
        live = self.this_open and self.rec is None
        off = mix(DIM, PANEL, 0.3)
        for text, cmd, tip in (("⟲ 10", lambda: self.nudge(-10),
                                "Back 10 s (←)"),
                               ("▶" if (self.playback or {}).get("paused")
                                else "❚❚", self.toggle_pause,
                                "Play / pause (Space)"),
                               ("10 ⟳", lambda: self.nudge(10),
                                "Forward 10 s (→)")):
            b = W.button(ctl, text, cmd, fg=FG if live else off, font=F_SM)
            b.pack(side="left", padx=(0, px(3)))
            self.tooltip.bind(b, tip)
        on = self._follow_on()
        b = W.button(ctl, "◎ Follow me", self.follow_me,
                     fg=GOOD if on else (FG if live else off),
                     bg=TEAL_BG if on else PANEL2, font=F_SM)
        b.pack(side="left", padx=(0, px(3)))
        self.tooltip.bind(b, "On: the camera stays on you after every jump "
                          "and comes back if it wanders off (F)")
        sp = W.TabRow(ctl, None, SPEEDS, float(self.prefs.get("speed", 1.0)),
                      on_change=self.set_speed, style="teal", bg=PANEL,
                      small=True)
        sp.pack(side="left", padx=(SP_M, 0))

    def _remote_key(self):
        pb = self.playback
        return (pb is not None, bool(pb and pb.get("paused")),
                self._follow_on(), self.this_open, self.opening,
                self.client_ok)

    def _follow_on(self) -> bool:
        return bool(self.prefs.get("follow", True))

    def _me(self):
        g = self.game
        return (g.me.champion, g.me.riot_id.split("#")[0]) if g else None

    # the selected clip
    def _render_clip(self):
        box = getattr(self, "clip_box", None)
        if box is None or not box.winfo_exists():
            return
        _clear(box)
        self.rec_lbl = self.rec_bar = None
        i = self.sel_i
        if i is None or not self.hl:
            W.section_title(box, "CLIP", EDGE_HI)
            tk.Label(box, text="No kills, assists or objectives of yours "
                     "in this game." if self.game and self.game.has_timeline
                     else "Pick a game (Games…).", bg=PANEL, fg=DIM,
                     font=F_SM, wraplength=px(520),
                     justify="left").pack(anchor="w")
            return
        h = self.hl[i]
        a, b = self.clip_range(i)
        W.section_title(box, "CLIP %d / %d  ·  %s" % (
            i + 1, len(self.hl), fmt_time(h.t_ms / 1000)), EDGE_HI,
            right="score %d" % h.score)
        tk.Label(box, text=h.title, bg=PANEL, fg=FG, font=F_MD_B,
                 anchor="w").pack(anchor="w")
        if len(h.beats) > 1 or h.beats[0].label != h.title:
            tk.Label(box, text=h.details, bg=PANEL, fg=DIM, font=F_XS,
                     wraplength=px(520), justify="left").pack(anchor="w")
        # in / out
        trim = tk.Frame(box, bg=PANEL)
        trim.pack(anchor="w", pady=(SP_M, 0))
        busy = self.rec is not None
        small = mix(DIM, PANEL, 0.4) if busy else DIM
        for label, which, ms in (("IN", "in", a), ("OUT", "out", b)):
            tk.Label(trim, text=label, bg=PANEL, fg=DIM, font=F_XS_B).pack(
                side="left", padx=(0, SP_XS))
            for txt, d in (("−1", -1000), (None, 0), ("+1", 1000)):
                if txt is None:
                    tk.Label(trim, text=fmt_time(ms / 1000), bg=PANEL2,
                             fg=FG, font=F_SM_B, padx=8, pady=2,
                             width=5).pack(side="left")
                    continue
                bb = W.button(trim, txt, lambda w=which, d=d:
                              self.nudge_edge(w, d), fg=small, font=F_XS)
                bb.configure(padx=6, pady=2)
                bb.pack(side="left")
                self.tooltip.bind(bb, "%s %s 1 s (%s sets it at the "
                                  "playhead)" % (label.title(), "earlier" if
                                                 d < 0 else "later",
                                                 label[0]))
            tk.Frame(trim, bg=PANEL, width=px(14)).pack(side="left")
        tk.Label(trim, text="LENGTH", bg=PANEL, fg=DIM, font=F_XS_B).pack(
            side="left", padx=(0, SP_XS))
        tk.Label(trim, text=_dur((b - a) / 1000), bg=PANEL, fg=FG,
                 font=F_SM_B).pack(side="left")
        if i in self.trims and not busy:
            r = W.button(trim, "Reset", self.reset_trim, fg=DIM, font=F_XS)
            r.configure(padx=6, pady=2)
            r.pack(side="left", padx=(SP_M, 0))
        # actions
        acts = tk.Frame(box, bg=PANEL)
        acts.pack(fill="x", pady=(SP_M, 0))
        if busy:
            self.rec_lbl = tk.Label(acts, text="", bg=PANEL, fg=FG, font=F_SM,
                                    anchor="w")
            self.rec_lbl.pack(side="left", fill="x", expand=True)
            W.button(acts, "Cancel", self.cancel_saving, fg=BAD,
                     font=F_SM).pack(side="right")
            self.rec_bar = W.ProgressBar(box, height=6)
            self.rec_bar.pack(fill="x", pady=(SP_S, 0))
            self.rec_bar.start()
            self._update_progress()
            return
        for text, cmd, tip in (("◀", lambda: self.step(-1),
                                "Previous clip (P)"),
                               ("▶", lambda: self.step(1), "Next clip (N)")):
            bb = W.button(acts, text, cmd, fg=DIM, font=F_SM)
            bb.pack(side="left", padx=(0, px(3)))
            self.tooltip.bind(bb, tip)
        bb = W.button(acts, "▷  Preview", self.preview, font=F_SM)
        bb.pack(side="left", padx=(SP_M, 0))
        self.tooltip.bind(bb, "Plays the clip in the replay, from in to "
                          "out, at normal speed (Enter)")
        saved = self.clip_path(i).exists()
        if saved:
            tk.Label(acts, text="✓ saved", bg=PANEL, fg=GOOD,
                     font=F_SM_B).pack(side="right", padx=(SP_M, 0))
        W.button(acts, "●  Save clip" if not saved else "●  Save again",
                 self.save, gold=not saved, font=F_SM).pack(side="right")
        tk.Label(box, text="League plays the clip once and records it: "
                 "about %s." % _dur((b - a) / 1000 + score.SETUP_S),
                 bg=PANEL, fg=DIM, font=F_XS).pack(anchor="e",
                                                   pady=(SP_XS, 0))

    # saved clips of this game
    def _render_saved(self):
        box = getattr(self, "saved_box", None)
        if box is None or not box.winfo_exists() or self.game is None:
            return
        _clear(box)
        folder = self.out_dir() / score.folder_name(self.game)
        files = _videos(folder)
        W.section_title(box, "SAVED CLIPS", GOOD, right="%d · click to play"
                        % len(files) if files else "")
        if not files:
            tk.Label(box, text="Clips you save show up here.", bg=PANEL,
                     fg=DIM, font=F_SM).pack(anchor="w")
        for f in files[:8]:
            self._clip_row(box, f)
        if len(files) > 8:
            tk.Label(box, text="+ %d more in the Clips view" % (
                len(files) - 8), bg=PANEL, fg=DIM, font=F_XS).pack(
                anchor="w")
        W.button(box, "Open folder", lambda: self.open_folder(folder),
                 font=F_SM, fg=DIM).pack(anchor="e", pady=(SP_M, 0))

    def _clip_row(self, box, f: Path):
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
        tk.Label(r, text=datetime.fromtimestamp(st.st_mtime).strftime(
            "%d %b %H:%M"), bg=PANEL, fg=DIM, font=F_XS, width=12,
            anchor="e").pack(side="right", padx=(0, px(6)))
        tk.Label(r, text=_mb(st.st_size), bg=PANEL, fg=DIM, font=F_XS,
                 width=8, anchor="e").pack(side="right")

        def paint(c, r=r):
            for w in (r, *r.winfo_children()):
                w.configure(bg=c)
        r.bind("<Enter>", lambda e: paint(HOVER))
        r.bind("<Leave>", lambda e: paint(PANEL))
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

    # ------------------------------------------------- editing a clip
    def pick(self, i: int, jump: bool = True):
        if self.rec is not None or not (0 <= i < len(self.hl)):
            return
        self.sel_i = i
        if self.tl:
            self.tl.select(i)
        self._render_clip()
        if jump:
            self.jump(self.clip_range(i)[0])

    def step(self, d: int):
        if not self.hl:
            return
        i = 0 if self.sel_i is None else (self.sel_i + d) % len(self.hl)
        self.pick(i)

    def trim(self, i: int, a: int, b: int):
        if self.rec is not None or not self.game:
            return
        end = self.game.duration_s * 1000
        a = int(min(max(a, 0), end - MIN_CLIP_MS))
        b = int(min(max(b, a + MIN_CLIP_MS), end))
        h = self.hl[i]
        if (a, b) == (h.start_ms, h.end_ms):
            self.trims.pop(i, None)
        else:
            self.trims[i] = (a, b)
        self.sel_i = i
        if self.tl:
            self.tl.selected = i
            self.tl.update_span(i, self._spans()[i])
        self._render_clip()
        self.set_status("Clip %s – %s (%s)" % (fmt_time(a / 1000), fmt_time(
            b / 1000), _dur((b - a) / 1000)))

    def nudge_edge(self, which: str, d_ms: int):
        if self.sel_i is None:
            return
        a, b = self.clip_range(self.sel_i)
        if which == "in":
            a += d_ms
        else:
            b += d_ms
        self.trim(self.sel_i, a, b)

    def set_edge(self, which: str):
        """I / O: put the clip's in or out where the replay is now."""
        if self.sel_i is None:
            return
        t = self._now_ms()
        if t is None:
            self.set_status("Open the replay first: in/out go where it is "
                            "playing", EDGE_HI)
            return
        a, b = self.clip_range(self.sel_i)
        t = int(t)
        if which == "in":
            a, b = t, max(b, t + MIN_CLIP_MS)
        else:
            a, b = min(a, t - MIN_CLIP_MS), t
        self.trim(self.sel_i, a, b)

    def reset_trim(self):
        if self.sel_i is not None and self.rec is None:
            h = self.hl[self.sel_i]
            self.trim(self.sel_i, h.start_ms, h.end_ms)

    # ---------------------------------------------- the replay remote
    def _replay_call(self, fn, quiet=False) -> bool:
        if self.rec is not None:      # never seek while a clip records
            return False
        if self.demo:
            if not quiet:
                self.set_status("Demo: no League here", EDGE_HI)
            return False
        if not self.this_open:
            if not quiet:
                self.set_status("Press “Watch replay” first", EDGE_HI)
            return False

        def work():
            try:
                fn()
            except local.LocalError:
                if not quiet:
                    self.post(self.set_status, "The replay isn't answering",
                              EDGE_HI)
        threading.Thread(target=work, daemon=True).start()
        return True

    def jump(self, ms: float, speed: float | None = None):
        sp = float(speed if speed is not None else self.prefs.get("speed",
                                                                  1.0))
        me = self._me() if self._follow_on() else None
        self.preview_out = None

        def go():
            self.rapi.set_playback(time=max(ms / 1000, 0), paused=False,
                                   speed=sp)
            if me:      # a seek clears the lock: lock once it's done
                self.rapi.follow_after_seek(*me)
        if self._replay_call(go) and self.tl:
            self.tl.set_playhead(ms)

    def seek_to(self, ms: float):
        self.jump(ms)

    def preview(self):
        if self.sel_i is None:
            return
        a, b = self.clip_range(self.sel_i)
        self.jump(a, speed=1.0)
        if self.this_open and self.rec is None and not self.demo:
            self.preview_out = b
            self.set_status("Preview %s – %s" % (fmt_time(a / 1000),
                                                 fmt_time(b / 1000)))

    def nudge(self, s):
        me = self._me() if self._follow_on() else None
        self.preview_out = None

        def go():
            pb = self.rapi.playback()
            self.rapi.set_playback(time=pb.get("time", 0) + s)
            if me:
                self.rapi.follow_after_seek(*me)
        self._replay_call(go)

    def toggle_pause(self):
        def go():
            pb = self.rapi.playback()
            self.rapi.set_playback(paused=not pb.get("paused", False))
        self._replay_call(go)

    def set_speed(self, v):
        self.prefs.set("speed", v)
        self._replay_call(lambda: self.rapi.set_playback(speed=v), quiet=True)

    def follow_me(self):
        on = not self._follow_on()
        self.prefs.set("follow", on)
        self._offscreen = 0
        self._render_remote()
        if not on:
            self.set_status("Camera is yours — Follow me is off", DIM)
            return
        me = self._me()
        if me:
            self._replay_call(lambda: self.rapi.follow(*me), quiet=True)

    def _now_ms(self) -> float | None:
        """Where the replay is now: the last poll moved on by the time
        since, at the replay's speed (no network call on the UI thread)."""
        pb = self.playback
        if pb is None or not self.this_open:
            return None
        t = pb.get("time", 0.0)
        if not pb.get("paused"):
            t += (time.monotonic() - getattr(self, "_pb_at",
                                             time.monotonic())) \
                * float(pb.get("speed") or 1.0)
        return t * 1000

    # ------------------------------------------------- client + replay
    def _check_replay(self):
        g = self.game
        if g is None or self.demo or self.old_patch(g.version):
            return
        gid, match = g.game_id, self.match

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
            except local.NotRunning:
                res = {"state": "offline", "text": ""}
            except local.LocalError as e:
                res = {"state": "clienterror", "text": "Client: %s" % e}
            self.post(done, res)

        def done(res):
            if self.game is None or self.game.game_id != gid:
                return
            self.replay_state = res
            self._render_remote()
        threading.Thread(target=work, daemon=True).start()

    def _check_open(self):
        """Is the open replay this game? (Same ten champions.)"""
        if self.demo or self.game is None:
            return
        champs, gid = self._champs, self.game.game_id

        def work():
            self.post(done, recorder._replay_is(self.rapi, champs))

        def done(ok):
            if self.game is None or self.game.game_id != gid:
                return
            if ok != self.this_open:
                self.this_open = ok
                self._render_remote()
                if self.tl and not ok:
                    self.tl.set_playhead(None)
        threading.Thread(target=work, daemon=True).start()

    def open_replay(self):
        """“Watch replay”: download if needed, start it, wait until it
        answers (the same path as REPLAY's Watch)."""
        g = self.game
        if g is None or self.opening or self.rec is not None or self.demo:
            return
        self.opening = True
        self.replay_state["msg"] = "Opening the replay…"
        self._render_remote()
        cancel = threading.Event()
        self._open_cancel = cancel

        def status(m):
            self.replay_state["msg"] = m
            self._render_remote()

        def work():
            err = None
            try:
                recorder.open_replay(self.lcu, self.rapi, g.game_id,
                                     self.match, self._champs, cancel,
                                     status=lambda m: self.post(status, m))
            except recorder.NotRecordable as e:
                err = "Can't play this replay: %s" % e
            except local.NotRunning:
                err = "Start the League client first"
            except recorder.Cancelled:
                err = "Cancelled"
            except (local.LocalError, TimeoutError, OSError) as e:
                err = str(e)
            self.post(done, err)

        def done(err):
            self.opening = False
            if err:
                self.set_status(err, BAD)
            elif self.game is g:
                self.this_open = True
                self.set_status("Replay open — click a block to jump to it",
                                GOOD)
                if self.sel_i is not None:
                    self.root.after(1500, lambda: self.sel_i is not None
                                    and self.jump(self.clip_range(
                                        self.sel_i)[0]))
            self._render_remote()
        threading.Thread(target=work, daemon=True).start()

    def _poll_local(self):
        """Every second (4× a second during a preview): client up, replay
        open, where it is; is it still this game (every 5 s)."""
        me = self._me() if self._follow_on() else None
        self._polls += 1
        check = self._polls % 5 == 1
        busy = self.rec is not None
        follow = self.this_open and not busy

        def work():
            ok = self.lcu.running()
            try:
                pb = self.rapi.playback()
            except local.LocalError:
                pb = None
            # bring the camera back — never while a clip records
            if pb is not None and me and follow and not pb.get("seeking"):
                self._keep_on_me(me)
            self.post(done, ok, pb)

        def done(ok, pb):
            was_ok, was_open = self.client_ok, self.playback is not None
            before = self._remote_key()
            self.client_ok, self.playback = ok, pb
            self._pb_at = time.monotonic()
            if pb is None:
                self.this_open = False
            self._banner()
            if ok != was_ok and self.game is not None:
                self._check_replay()
            if pb is not None and (not was_open or check):
                self._check_open()
            if self._remote_key() != before:
                self._render_remote()
            if self.tl:
                self.tl.set_playhead(pb.get("time", 0) * 1000
                                     if pb is not None and self.this_open
                                     else None)
            if self.preview_out is not None and pb is not None and \
                    pb.get("time", 0) * 1000 >= self.preview_out:
                self.preview_out = None
                self._replay_call(lambda: self.rapi.set_playback(
                    paused=True), quiet=True)
            if self._alive:
                self.root.after(250 if self.preview_out else 1000,
                                self._poll_local)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------ saving
    def save(self):
        g = self.game
        i = self.sel_i
        if g is None or i is None or self.rec is not None:
            return
        if self.old_patch(g.version):
            self.set_status("Can't save: this game is on an old patch and "
                            "Riot only keeps replays for the current patch",
                            BAD)
            return
        a, b = self.clip_range(i)
        clip = Clip(a / 1000, b / 1000, self.clip_path(i), self.hl[i].title)
        self.cancel = cancel = threading.Event()
        self.preview_out = None
        self.rec = {"clip": clip, "i": i, "p": Progress("loading", 0, 1),
                    "msg": ""}
        self.results = []
        if self.tl:
            self.tl.locked = True
            self.tl.set_progress(0.0)
        self._render_clip()
        self._render_remote()
        self.set_status("Saving %s — League plays it once (about %s)" % (
            clip.target.name, _dur(clip.end_s - clip.start_s
                                   + score.SETUP_S)))
        me = self._me()
        prog = lambda p: self.post(self._on_progress, p)    # noqa: E731
        is_open = self.this_open

        def work():
            err = None
            res: list[ClipResult] = []
            try:
                if self.demo:
                    res = _simulate([clip], prog, cancel)
                else:
                    if not is_open:
                        recorder.open_replay(
                            self.lcu, self.rapi, g.game_id, self.match,
                            self._champs, cancel,
                            status=lambda m: self.post(self._on_load, m))
                    rec = recorder.Recorder(self.rapi, *me, on_progress=prog,
                                            cancel=cancel)
                    res = rec.record_all([clip])
            except recorder.Cancelled:
                err = "Cancelled"
            except recorder.NotRecordable as e:
                err = ("Can't record this game: %s. Riot keeps replays only "
                       "for the current patch." % e)
            except local.NotRunning:
                err = "Start the League client first, then save again"
            except (local.LocalError, TimeoutError, OSError) as e:
                err = str(e)
            except Exception as e:                  # never leave it hanging
                self._log(traceback.format_exc())
                err = "%s: %s" % (e.__class__.__name__, e)
            self.post(self._finished, res, err, cancel.is_set())
        threading.Thread(target=work, daemon=True).start()

    def cancel_saving(self):
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
        if self.tl and p.phase in ("recording", "saving"):
            self.tl.set_progress(p.frac)
        self._update_progress()

    def _update_progress(self):
        r = self.rec
        lbl, bar = getattr(self, "rec_lbl", None), getattr(self, "rec_bar",
                                                            None)
        if r is None or lbl is None or not lbl.winfo_exists():
            return
        p: Progress = r["p"]
        c: Clip = r["clip"]
        if p.phase == "loading":
            text = r["msg"] or "Getting the replay ready…"
        else:
            text = {"seeking": "Jumping to the clip…",
                    "camera": "Camera on you…",
                    "window": "Click League's replay window — it has to be "
                    "on screen to record",
                    "recording": "● Recording  %s left" % _dur(
                        (1 - p.frac) * (c.end_s - c.start_s)),
                    "saving": "Saving the file…",
                    "error": "Failed: %s" % p.message, "done": "Done",
                    "cancelled": "Cancelled"}.get(p.phase, p.phase)
        lbl.configure(text=text, fg=BAD if p.phase in ("error", "recording")
                      else FG)
        if bar is not None:
            bar.set(p.frac if p.phase in ("recording", "saving") else 0.0)

    def _finished(self, results: list[ClipResult], err: str | None,
                  cancelled: bool):
        self.rec = None
        self.cancel = None
        self.results = results
        if self.tl:
            self.tl.locked = False
            self.tl.set_progress(None)
        ok = [r for r in results if r.ok]
        bad = [r for r in results if not r.ok]
        if self.demo and not err:
            self.set_status("Demo: the clip would have been saved — nothing "
                            "was recorded", EDGE_HI)
        elif err and not ok:
            self.set_status(err, EDGE_HI if cancelled else BAD)
        elif bad:
            self.set_status("Couldn't save: %s" % bad[0].error, BAD)
        else:
            self.set_status("Saved %s" % ok[0].path.name, GOOD)
        if self.view == "editor" and self.game is not None:
            if self.tl:
                self.tl.set(self.game.duration_s * 1000, self.game.moments,
                            self._spans(), self.sel_i)
            self._render_clip()
            self._render_saved()
            self._render_remote()

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
            tk.Label(p, text="No clips yet. In the Editor, click a block on "
                     "the timeline, trim it and press Save clip.",
                     bg=PANEL, fg=DIM, font=F_SM).pack(anchor="w",
                                                       pady=(SP_M, 0))
        for _, d, fs in games:
            W.caption(p, d.name.replace("_", "  ").upper())
            for f in fs:
                self._clip_row(p, f)

    # ------------------------------------------------------ keyboard
    def _bind_keys(self):
        def guard(fn):
            def h(e):
                if isinstance(e.widget, (tk.Text, tk.Entry)):
                    return None
                if self.view == "editor":
                    fn()
                return "break"
            return h
        r = self.root
        r.bind("<space>", guard(self.toggle_pause))
        r.bind("<Left>", guard(lambda: self.nudge(-10)))
        r.bind("<Right>", guard(lambda: self.nudge(10)))
        r.bind("<Return>", guard(self.preview))
        for keys, fn in ((("n", "N"), lambda: self.step(1)),
                         (("p", "P"), lambda: self.step(-1)),
                         (("i", "I"), lambda: self.set_edge("in")),
                         (("o", "O"), lambda: self.set_edge("out")),
                         (("s", "S"), self.save),
                         (("f", "F"), self.follow_me)):
            for k in keys:
                r.bind("<%s>" % k, guard(fn))

    # ------------------------------------------------------ settings
    def show_settings(self):
        d = tk.Toplevel(self.root, bg=BORDER)
        d.title("Highlights settings")
        d.transient(self.root)
        body = tk.Frame(d, bg=PANEL, padx=px(16), pady=px(12))
        body.pack(padx=1, pady=1)
        W.section_title(body, "SETTINGS", EDGE_HI)
        W.caption(body, "VIDEO", pady=(0, SP_XS))
        tk.Label(body, text="League records its own window: the clip is as "
                 "big as League's window, in real time. Run League "
                 "borderless (Video → Window mode) to keep the replay and "
                 "this window side by side; in full screen Highlights "
                 "brings the replay to the front while it records.",
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
            if getattr(self, "_open_cancel", None) is not None:
                self._open_cancel.set()
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
    """--demo: the saving flow without a game (nothing is written)."""
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
        out.append(ClipResult(c, True, c.target))
    prog(Progress("done", n, n, 1.0))
    return out
