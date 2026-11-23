"""REPLAY desktop app — DRAFT's window, a replay remote and a notebook.

Layout: banner (title · account · client/replay status · ⚙), VIEW tabs,
then one of three views:

* Review   — your games (left) and the open game (right): replay controls,
             the timeline, the moments to tag, the tag editor and the
             matchup note.
* Insights — why you die and what you miss, over every tagged game.
* Matchups — every matchup note, newest first.

Matches and timelines come from Review's cache (``review.sqlite``); notes
live in ``replay.sqlite`` next to it. All network and disk work runs off
the UI thread and comes back through ``post``.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime

from review import config, static
from review.cache import Cache
from review.riot_client import get_region
from review.theme import anim
from review.theme import widgets as W
from review.theme import window
from review.theme.tokens import (ACCENT, BAD, BORDER, DARK_BG, DIM, EDGE,
                                 EDGE_HI, F_LG_B, F_MD, F_SM, F_SM_B, F_XS,
                                 F_XS_B, FG, GOOD, HOVER, PANEL, PANEL2,
                                 SP_L, SP_M, SP_S, SP_XS, TEAL_BG, WIN, mix)
from review.theme.widgets import px
from review.ui.assets import IconStore

from . import local, rofl
from .events import GameView, build_game, context_lines, is_remake
from .model import (AVOIDABLE, BOOKMARK, CATEGORIES, DEATH,
                    DEATH_CATEGORIES, KINDS, LANE_RATING, MISSED,
                    MISSED_CATEGORIES, category_label, fmt_time)
from .notes import Mark, Matchup, Notes, notes_path
from .widgets import (AMBER, MARK_COLOR, Chips, MiniMap, MomentList,
                      MomentRow, TimelineBar)

VIEWS = [("review", "Review"), ("insights", "Insights"),
         ("matchups", "Matchups")]
QUEUES = (420, 440)
QUEUE_SHORT = {420: "Solo", 440: "Flex"}
SPEEDS = [(0.5, "0.5×"), (1.0, "1×"), (2.0, "2×"), (4.0, "4×")]
ROLE_SHORT = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid",
              "BOTTOM": "Bot", "UTILITY": "Support"}
LEGAL = ("REPLAY isn't endorsed by Riot Games and doesn't reflect the views "
         "or opinions of Riot Games or anyone officially involved in "
         "producing or managing Riot Games properties.")


class Prefs:
    def __init__(self):
        self.path = config.data_dir() / "replay_prefs.json"
        try:
            self.d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v
        try:
            self.path.write_text(json.dumps(self.d), encoding="utf-8")
        except OSError:
            pass


def _date(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%d %b")
    except (OSError, ValueError, OverflowError):
        return ""


def _clear(frame):
    for w in frame.winfo_children():
        w.destroy()


def panel(parent, title=None, color=EDGE_HI, right=None, **pack):
    p = W.CutPanel(parent, padx=14, fill=True)
    p.pack(**({"fill": "x"} | pack))
    rl = W.section_title(p.inner, title, color, right=right or "") \
        if title else None
    return p.inner, rl


def text_box(parent, height=4, width=40):
    t = tk.Text(parent, height=height, width=width, bg=PANEL2, fg=FG,
                insertbackground=EDGE_HI, relief="flat", font=F_MD,
                wrap="word", highlightthickness=1, highlightbackground=BORDER,
                highlightcolor=EDGE_HI, selectbackground=EDGE,
                selectforeground=FG, padx=px(6), pady=px(4), undo=True)
    return t


class GameRow:
    """What the games list needs, without the timeline."""
    __slots__ = ("match_id", "game_id", "champion", "role", "opponent",
                 "win", "kda", "deaths", "created", "duration", "queue",
                 "version", "remake")

    def __init__(self, match: dict, puuid: str):
        info = match["info"]
        me = next(p for p in info["participants"] if p.get("puuid") == puuid)
        opp = [p for p in info["participants"]
               if p.get("teamPosition") and p.get("teamPosition")
               == me.get("teamPosition") and p["teamId"] != me["teamId"]]
        self.match_id = match["metadata"]["matchId"]
        self.game_id = int(info.get("gameId") or
                           self.match_id.rsplit("_", 1)[1])
        self.champion = me.get("championName", "?")
        self.role = me.get("teamPosition") or None
        self.opponent = opp[0].get("championName") if len(opp) == 1 else None
        self.win = bool(me.get("win"))
        self.kda = "%d / %d / %d" % (me.get("kills", 0), me.get("deaths", 0),
                                     me.get("assists", 0))
        self.deaths = me.get("deaths", 0)
        self.created = info.get("gameCreation", 0)
        self.duration = info.get("gameDuration", 0)
        self.queue = info.get("queueId", 0)
        self.version = info.get("gameVersion", "")
        self.remake = is_remake(info)


def result_cell(g) -> tuple[str, str]:
    """Games-list result: W / L, or a grey R for a remake."""
    if g.remake:
        return "R", DIM
    return ("W", WIN) if g.win else ("L", BAD)


def result_text(g) -> tuple[str, str]:
    """Game header: Victory / Defeat, or Remake in grey."""
    if g.remake:
        return "Remake", DIM
    return ("Victory", WIN) if g.win else ("Defeat", BAD)


class App:
    def __init__(self, demo: bool = False):
        self.root = root = W.make_root("Replay")
        try:      # DRAFT's diamond with a play triangle
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
        self.notes = Notes(notes_path())
        self.install = local.install_dir(self.prefs.get("league_dir"))
        self.lcu = local.LeagueClient(self.install)
        self.rapi = local.ReplayAPI()
        self.player: dict | None = None
        self.games: list[GameRow] = []
        self.rofls: dict[str, rofl.RoflFile] = {}
        self.current_patch: str | None = None
        self.game: GameView | None = None
        self.marks: list[Mark] = []
        self.sel_mark: int | None = None
        self.view = "review"
        self.client_ok = False
        self.playback: dict | None = None      # last /replay/playback
        self.replay_state: dict = {}
        self.job = None
        self._save_job = None
        self._inbox: queue.Queue = queue.Queue()
        self._alive = True                     # False once root is destroyed
        self._offscreen = 0                    # polls with you off screen
        self._build()
        self.root.after(30, self._drain)
        root.geometry(window.safe_geometry(
            root, self.prefs.get("geometry"), (1320, 900)))
        root.minsize(px(self.MIN_W), px(self.MIN_H))
        root.protocol("WM_DELETE_WINDOW", self.close)
        # stop the repeating timers once the window is gone (otherwise they
        # keep re-arming on the dead Tcl interpreter)
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
        self.banner = W.Banner(outer, "REPLAY", on_gear=self.show_settings)
        self.banner.pack(fill="x")
        nav = tk.Frame(outer, bg=DARK_BG)
        nav.pack(fill="x", pady=(SP_L, SP_M))
        self.nav = W.TabRow(nav, "VIEW", VIEWS, "review",
                            on_change=self.navigate)
        self.nav.pack(side="left")
        # pady=4 like the tabs: taller buttons would push the tab row 2 px
        # below DRAFT's and the panels 4 px
        self.fetch_btn = W.button(nav, "Fetch new games", self.fetch,
                                  fg=DIM, font=F_SM)
        self.fetch_btn.configure(pady=4)
        self.fetch_btn.pack(side="right")
        acc = W.button(nav, "Account", self.ask_account, fg=DIM, font=F_SM)
        acc.configure(pady=4)
        acc.pack(side="right", padx=(0, SP_S))
        # Review view only: hide the games list while reviewing one game
        self.side_btn = W.button(nav, "", self.toggle_games, fg=DIM,
                                 font=F_SM)
        self.side_btn.configure(pady=4)
        self._side_label()
        self.content = tk.Frame(outer, bg=DARK_BG)
        self.content.pack(fill="both", expand=True)
        bottom = tk.Frame(outer, bg=DARK_BG)
        bottom.pack(fill="x", side="bottom", pady=(SP_M, 0))
        self.keys_lbl = tk.Label(
            bottom, text="Space play/pause · ←/→ 10 s · N next untagged · "
            "M missed chance · B bookmark · F follow me · G games list",
            bg=DARK_BG,
            fg=mix(DIM, DARK_BG, 0.35), font=F_XS, anchor="w")
        self.status = tk.Label(bottom, text="", bg=DARK_BG, fg=DIM, font=F_SM)
        # status first: in a narrow window the key hints get cut, not it
        self.status.pack(side="right", anchor="s", padx=(SP_M, 0))
        self.keys_lbl.pack(side="left", anchor="s", fill="x", expand=True)
        self.navigate("review")

    def set_status(self, text, color=DIM):
        self.status.configure(text=text, fg=color)

    def post(self, fn, *args):
        """Run ``fn(*args)`` on the UI thread. Safe from any thread, also
        before mainloop starts (Tk's own after() isn't: from a worker it
        raises "main thread is not in main loop" and the call is lost)."""
        self._inbox.put((fn, args))

    def _drain(self):
        try:
            while True:
                fn, args = self._inbox.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    self._log(traceback.format_exc())
        except queue.Empty:
            pass
        if not self._alive:
            return
        try:
            self.root.after(30, self._drain)
        except tk.TclError:
            pass

    def _banner(self):
        bits = []
        if self.player:
            bits.append("%s · %s" % (self.player["riot_id"],
                                     self.player["region"]))
        bits.append("client " + ("connected" if self.client_ok
                                 else "not running"))
        if self.playback:
            bits.append("replay %s%s" % (fmt_time(self.playback.get(
                "time", 0)), " ❚❚" if self.playback.get("paused") else " ▶"))
        self.banner.set_context(" · ".join(bits))

    def navigate(self, name):
        self.view = name
        self.nav.select(name, fire=False)
        _clear(self.content)
        self.tl = None
        if name == "review":
            self.side_btn.pack(side="left", padx=(SP_L, 0))
        else:
            self.side_btn.pack_forget()
        getattr(self, "_view_" + name)()

    # ------------------------------------------------- games list (drawer)
    # Like a chat app's sidebar: in a wide window the list is docked next
    # to the game (shown/hidden is remembered); in a narrow one it is
    # collapsed and "Show games" slides it in over the game, picking a game
    # slides it away. The window never has to grow for it.
    MIN_W, MIN_H = 660, 680
    LIST_W = 440
    DOCK_AT, UNDOCK_AT = 1100, 1080      # wrap width (logical px), hysteresis
    SLIDE_MS, SLIDE_STEPS = 170, 10
    # until the review view first lays out (set again in _view_review)
    _docked, _overlay_open, _dock_p = True, False, 1.0

    def _games_hidden(self) -> bool:
        return bool(self.prefs.get("games_hidden", False))

    def games_shown(self) -> bool:
        """Where the drawer is heading: docked-and-shown, or slid in."""
        if self._docked:
            return not self._games_hidden()
        return self._overlay_open

    def _side_label(self):
        self.side_btn.configure(text="‹  Hide games" if self.games_shown()
                                else "›  Show games")

    def toggle_games(self):
        """G / the nav button. Wide window: dock or hide the list (and
        remember it). Narrow window: slide it in over the game, or away."""
        if self.view != "review":
            return
        if self._docked:
            self.prefs.set("games_hidden", not self._games_hidden())
        else:
            self._overlay_open = not self._overlay_open
        self._side_label()
        self._slide(1.0 if self.games_shown() else 0.0)

    def close_games_overlay(self):
        if not self._docked and self._overlay_open:
            self._overlay_open = False
            self._side_label()
            self._slide(0.0)

    def _wrap_resized(self, e):
        w = e.width / px(1)
        docked = w >= self.UNDOCK_AT if self._docked else w >= self.DOCK_AT
        if docked != self._docked:            # crossed the breakpoint: snap
            self._docked, self._overlay_open = docked, False
            self._side_label()
            self._dock_p = 1.0 if self.games_shown() else 0.0
        self._place_games()

    def _place_games(self):
        """Lay out list and game for the drawer position ``_dock_p``."""
        left, right = self.games_left, self.right
        if not (left.winfo_exists() and right.winfo_exists()):
            return
        lw, gap = px(self.LIST_W), SP_L
        p = self._dock_p
        if p <= 0:
            left.place_forget()
        else:
            left.place(x=round(-lw * (1 - p)), y=0, width=lw, relheight=1)
        shift = round((lw + gap) * p) if self._docked else 0
        right.place(x=shift, y=0, relwidth=1, width=-shift, relheight=1)
        if not self._docked:
            left.lift()                        # slides in over the game

    def _after_slide(self, fn):
        """Run ``fn`` now, or when the drawer has finished sliding (a
        page rebuild mid-slide makes the slide stutter)."""
        if getattr(self, "_slide_job", None):
            self._after_slide_fns = getattr(self, "_after_slide_fns", [])
            self._after_slide_fns.append(fn)
        else:
            fn()

    def _slide(self, target: float):
        job = getattr(self, "_slide_job", None)
        if job:
            self.root.after_cancel(job)
            self._slide_job = None
        start, n = self._dock_p, self.SLIDE_STEPS

        def step(k=1):
            self._slide_job = None
            if not self.games_left.winfo_exists():
                return
            t = k / n
            self._dock_p = start + (target - start) * anim.ease_out(t)
            if k >= n:
                self._dock_p = target
            self._place_games()
            if k < n:
                self._slide_job = self.root.after(
                    self.SLIDE_MS // n, lambda: step(k + 1))
            else:
                fns, self._after_slide_fns = getattr(
                    self, "_after_slide_fns", []), []
                for fn in fns:
                    fn()
        step()

    # -------------------------------------------------------- data in
    def _load_static(self):
        try:
            static.refresh_champions()
            ver = static.latest_version()
            static.minimap()
        except Exception:
            ver = None

        def done():
            if ver:
                self.current_patch = ".".join(ver.split(".")[:2])
                if self.view == "review" and self.games:
                    self._fill_games()
        self.post(done)

    def _known_players(self):
        with self.cache._lock:
            rows = self.cache._db.execute(
                "SELECT puuid, game_name, tag_line, region FROM players "
                "ORDER BY last_synced DESC").fetchall()
        return [{"puuid": r[0], "riot_id": "%s#%s" % (r[1], r[2]),
                 "region": r[3]} for r in rows]

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
            ids = self.cache.player_match_ids(puuid, QUEUES, limit=150)
            rows = []
            for mid in ids:
                m = self.cache.get_match(mid)
                if m:
                    try:
                        rows.append(GameRow(m, puuid))
                    except (StopIteration, KeyError, ValueError):
                        pass
            folder = self.prefs.get("replays_dir")
            fs = rofl.scan(folder) if folder else rofl.scan()
            self.post(done, rows, fs)

        def done(rows, fs):
            self.games, self.rofls = rows, fs
            self.set_status("%d games · %d replays on disk" % (
                len(rows), sum(1 for g in rows if g.match_id in fs)))
            if self.view == "review":
                self._fill_games()
            if then:
                then()
            elif self.game is None and rows:
                self.open_game(rows[0].match_id)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- the account
    def ask_account(self):
        d = tk.Toplevel(self.root, bg=EDGE)
        d.title("Account")
        d.transient(self.root)
        body = tk.Frame(d, bg=PANEL, padx=px(16), pady=px(12))
        body.pack(padx=1, pady=1)
        W.section_title(body, "ACCOUNT", EDGE_HI)
        known = self._known_players()
        if known:
            W.caption(body, "FROM REVIEW", pady=(0, SP_XS))
            for k in known[:6]:
                b = W.button(body, k["riot_id"] + "  ·  " + k["region"],
                             lambda k=k: (d.destroy(), self.switch(k)),
                             font=F_SM)
                b.pack(anchor="w", fill="x", pady=1)
        W.caption(body, "OR TYPE A RIOT ID (NAME#TAG)")
        e = W.entry(body, 30)
        e.pack(anchor="w")
        reg = W.TabRow(body, "REGION", [(r, r) for r in ("EUW", "EUNE", "NA",
                                                         "KR")],
                       (self.player or {}).get("region", "EUW"), bg=PANEL)
        reg.pack(anchor="w", pady=(SP_M, 0))

        def go():
            rid = e.get().strip()
            if "#" not in rid:
                e.configure(highlightbackground=BAD)
                return
            d.destroy()
            self.fetch(rid, reg.value)
        W.button(body, "Fetch games", go, gold=True).pack(anchor="e",
                                                         pady=(SP_L, 0))
        e.bind("<Return>", lambda ev: go())
        e.focus_set()

    def switch(self, p):
        self.game = None
        self.set_player(p)

    def fetch(self, riot_id: str | None = None, region: str | None = None):
        """Bring the latest ranked games (and timelines) into the cache
        with Review's own downloader and the shared key."""
        if self.job is not None:
            return
        from review.riot_client import (ReviewError, default_client)
        from review.sync import SyncJob
        if riot_id is None:
            if not self.player:
                self.ask_account()
                return
            riot_id, region = self.player["riot_id"], self.player["region"]
        try:
            job = SyncJob(default_client(), self.cache, riot_id,
                          get_region(region), queues=(420,),
                          count=int(self.prefs.get("fetch_count", 30)),
                          on_progress=lambda p: self.post(self._progress, p))
        except (ReviewError, ValueError) as e:
            self.set_status(str(e), BAD)
            return
        self.job = job
        self.set_status("Fetching %s…" % riot_id)
        job.start()

        def poll():
            if job._thread and job._thread.is_alive():
                self.root.after(300, poll)
                return
            self.job = None
            if job.error is not None:
                self.set_status(str(job.error) or job.error.__class__.__name__,
                                BAD)
                return
            r = job.result
            self.set_status("Fetched %d new games" % r.matches_fetched, GOOD)
            p = {"puuid": r.puuid, "riot_id": r.riot_id, "region": r.region}
            if not self.player or self.player["puuid"] != r.puuid:
                self.game = None
                self.set_player(p)
            else:
                self.reload_games(then=lambda: self.game and self.open_game(
                    self.game.match_id, keep_sel=True))
        self.root.after(300, poll)

    def _progress(self, p):
        if p.phase == "games" and p.total:
            self.set_status("Downloading games %d/%d…" % (p.done, p.total))
        elif p.message:
            self.set_status(p.message)

    # ====================================================== REVIEW view
    def _view_review(self):
        wrap = tk.Frame(self.content, bg=DARK_BG)
        wrap.pack(fill="both", expand=True)
        # list and game are place()d (see _place_games) so the list can
        # slide; docked or overlay is decided by the width (_wrap_resized)
        left = self.games_left = tk.Frame(wrap, bg=DARK_BG)
        ww = self.root.winfo_width() / px(1)
        self._docked = ww >= self.DOCK_AT + 40      # wrap ≈ window - 40
        self._overlay_open = False
        self._dock_p = 1.0 if self.games_shown() else 0.0
        self._slide_job = None
        p = W.CutPanel(left, padx=12, grow=True)
        p.pack(fill="both", expand=True)
        self.games_right = W.section_title(p.inner, "GAMES", EDGE_HI,
                                           right="")
        cols = [W.Col("icon", "", 0, kind="icon"),
                W.Col("champ", "CHAMPION", 8, "w", stretch=True),
                W.Col("vs", "VS", 7, "w"),
                W.Col("res", "", 1),
                W.Col("date", "DATE", 7, "e"),
                W.Col("tag", "TAGGED", 5, "center"),
                W.Col("rep", "REPLAY", 8, "center")]
        self.games_tbl = W.Table(p.inner, cols, icon_loader=self.icons.get,
                                 on_click=lambda r: self.open_game(
                                     r.data.match_id),
                                 rows_visible=None, tooltip=self.tooltip)
        self.games_tbl.pack(fill="both", expand=True)
        self.right = W.ScrollArea(wrap)
        self._side_label()
        self._place_games()
        wrap.bind("<Configure>", self._wrap_resized)
        self._fill_games()
        self._render_game()

    def _replay_cell(self, g: GameRow):
        f = self.rofls.get(g.match_id)
        patch = ".".join(g.version.split(".")[:2])
        if f is not None:
            if self.current_patch and patch != self.current_patch:
                return ("old patch", DIM)
            return ("▶ ready", GOOD)
        if self.current_patch and patch != self.current_patch:
            return ("—", mix(DIM, PANEL, 0.4))
        return ("download", EDGE_HI)

    def _fill_games(self):
        if not getattr(self, "games_tbl", None) or not \
                self.games_tbl.winfo_exists():
            return
        prog = self.notes.progress(self.player["puuid"]) if self.player \
            else {}
        reviewed = self.notes.reviewed_games(self.player["puuid"]) \
            if self.player else set()
        rows = []
        for g in self.games:
            done, n = prog.get(g.match_id, (0, 0))
            n = max(n, g.deaths)
            if g.match_id in reviewed:
                tag = ("✓", GOOD)
            elif n:
                tag = ("%d/%d" % (done, n), GOOD if done >= n else (
                    EDGE_HI if done else DIM))
            else:
                tag = ("—", DIM)
            rows.append(W.Row({
                "champ": (g.champion, FG),
                "vs": (g.opponent or "", DIM),
                "res": result_cell(g),
                "date": (_date(g.created), DIM),
                "tag": tag,
                "rep": self._replay_cell(g)},
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
        if self.view == "review" and hasattr(self, "_docked"):
            self.close_games_overlay()
        puuid = self.player["puuid"]
        prev_sel = self.sel_mark if keep_sel else None

        def work():
            m = self.cache.get_match(match_id)
            tl = self.cache.get_timeline(match_id)
            try:
                g = build_game(m, tl, puuid)
            except Exception as e:
                self.post(self.set_status, "Can't open %s: %s" % (
                    match_id, e), BAD)
                return
            self.notes.ensure_deaths(match_id, puuid,
                                     [d.t_ms for d in g.deaths_list],
                                     g.me.champion)
            if self.notes.matchup(match_id, puuid) is None:
                self.notes.save_matchup(Matchup(
                    match_id, puuid, g.me.champion,
                    g.opponent.champion if g.opponent else None,
                    g.ally_support.champion if g.ally_support else None,
                    g.enemy_support.champion if g.enemy_support else None))
            marks = self.notes.marks(match_id, puuid)
            self.post(done, g, marks)

        def done(g, marks):
            self.game, self.marks = g, marks
            first = next((mk.id for mk in marks if not mk.tagged), None)
            ids = {mk.id for mk in marks}
            self.sel_mark = prev_sel if prev_sel in ids else (
                first if first is not None else (marks[0].id if marks
                                                 else None))
            self.replay_state = {}
            if self.view != "review":
                self.navigate("review")
            else:
                self._fill_games()
                self._after_slide(self._render_game)
            self._check_replay()
        threading.Thread(target=work, daemon=True).start()

    def _render_game(self):
        """Show ``self.game``. The page (header, timeline, moments, tag
        editor, matchup) is built once and then refilled for each game:
        on Windows every widget is a native window, and building, laying
        out and destroying ~120 of them per switch froze the UI for half a
        second. A new page is built off-screen and swapped in whole; the
        old one is torn down afterwards a panel at a time."""
        if not getattr(self, "right", None) or not self.right.winfo_exists():
            return
        g = self.game
        pg = getattr(self, "_page", None)
        if g is not None and pg and pg["body"] is self.right.body \
                and pg["body"].winfo_exists():
            self._fill_page(g)
            return
        body = self.right.fresh_body()
        self.tl = None
        self._moment_list = self._tag_ed = self._page = None
        if g is None:
            p, _ = panel(body, "GAME")
            tk.Label(p, text="Pick a game on the left. New games: "
                     "“Fetch new games” (uses Review's key and cache).",
                     bg=PANEL, fg=DIM, font=F_SM).pack(anchor="w")
        else:
            self._page = {"body": body}
            self._game_header(body)
            self._timeline_panel(body)
            row = tk.Frame(body, bg=DARK_BG)
            row.pack(fill="x", pady=(SP_M, 0))
            row.grid_columnconfigure(0, weight=2, uniform="r")
            row.grid_columnconfigure(1, weight=3, uniform="r")
            lp = W.CutPanel(row, padx=14, fill=True)
            lp.grid(row=0, column=0, sticky="nsew", padx=(0, SP_M))
            rp = W.CutPanel(row, padx=14, fill=True)
            rp.grid(row=0, column=1, sticky="nsew")
            self.moments_box, self.tag_box = lp.inner, rp.inner
            self._matchup_panel(body)
            self._fill_page(g)
        old = self.right.swap_body(body)
        self._destroy_gently(old)

    def _fill_page(self, g: GameView):
        self._fill_header(g)
        self._fill_timeline(g)
        self._render_moments()
        self._render_tag()
        self._fill_matchup(g)

    def _destroy_gently(self, w):
        kids = list(w.winfo_children())

        def step():
            if not w.winfo_exists():
                return
            if kids:
                k = kids.pop()
                if k.winfo_exists():
                    k.destroy()
                self.root.after(15, step)
            else:
                w.destroy()
        self.root.after(30, step)

    # header + replay remote
    def _game_header(self, body):
        pg = self._page
        p, pg["hdr_right"] = panel(body, "GAME", right="")
        top = tk.Frame(p, bg=PANEL)
        top.pack(fill="x")
        pg["icon"] = tk.Label(top, bg=PANEL)
        pg["icon"].pack(side="left", padx=(0, SP_L))
        t = pg["hdr_text"] = tk.Frame(top, bg=PANEL)
        t.pack(side="left")
        pg["title"] = tk.Label(t, bg=PANEL, fg=FG, font=F_LG_B)
        pg["title"].pack(anchor="w")
        pg["sub"] = tk.Label(t, bg=PANEL, font=F_SM)
        pg["sub"].pack(anchor="w")
        # replay remote
        self.remote = tk.Frame(top, bg=PANEL)
        self.remote.pack(side="right")

    def _fill_header(self, g: GameView):
        pg = self._page
        pg["hdr_right"].configure(text="%s · patch %s · %s" % (
            QUEUE_SHORT.get(g.queue, g.queue), g.patch, _date(g.created_ms)))
        ic = self.icons.get(g.me.champion, px(44),
                            lambda: self._render_game())
        pg["icon_ref"] = ic
        if ic is not None:
            pg["icon"].configure(image=ic)
            pg["icon"].pack(side="left", padx=(0, SP_L),
                            before=pg["hdr_text"])
        else:
            pg["icon"].pack_forget()
        name = static.champion_name(g.me.champion)
        vs = static.champion_name(g.opponent.champion) if g.opponent else "?"
        pg["title"].configure(text="%s  vs  %s" % (name, vs))
        res, res_fg = result_text(g)
        pg["sub"].configure(text="%s · %s · %s · %d CS · %s" % (
            res, ROLE_SHORT.get(g.me.role or "", "?"),
            g.kda_text, g.cs, fmt_time(g.duration_s)), fg=res_fg)
        self._render_remote()

    def _render_remote(self):
        box = getattr(self, "remote", None)
        if box is None or not box.winfo_exists() or self.game is None:
            return
        _clear(box)
        st = self.replay_state
        line = tk.Frame(box, bg=PANEL)
        line.pack(anchor="e")
        state = st.get("state")
        txt = st.get("text", "Checking replay…")
        col = GOOD if state == "watch" else (
            EDGE_HI if state in ("download", "downloading", "retryDownload")
            else DIM)
        tk.Label(line, text=txt, bg=PANEL, fg=col, font=F_SM).pack(
            side="left", padx=(0, SP_M))
        if state in ("download", "retryDownload"):
            W.button(line, "Download", self.download, gold=True).pack(
                side="left")
        elif state == "watch":
            W.button(line, "▶  Watch", self.watch, gold=True).pack(
                side="left")
        ctl = tk.Frame(box, bg=PANEL)
        ctl.pack(anchor="e", pady=(SP_S, 0))
        live = self.playback is not None
        for text, cmd, tip in (("⟲ 10", lambda: self.nudge(-10),
                                "Back 10 s (←)"),
                               ("❚❚" if not (self.playback or {}).get(
                                   "paused") else "▶", self.toggle_pause,
                                "Play / pause (Space)"),
                               ("10 ⟳", lambda: self.nudge(10),
                                "Forward 10 s (→)")):
            b = W.button(ctl, text, cmd, fg=FG if live else mix(DIM, PANEL,
                                                                 0.3),
                         font=F_SM)
            b.pack(side="left", padx=(0, px(3)))
            self.tooltip.bind(b, tip)
        on = self._follow_on()
        b = W.button(ctl, "◎ Follow me", self.follow_me,
                     fg=GOOD if on else (FG if live else mix(DIM, PANEL, 0.3)),
                     bg=TEAL_BG if on else PANEL2, font=F_SM)
        b.pack(side="left", padx=(0, px(3)))
        self.tooltip.bind(b, "On: the camera stays on you — after every "
                          "jump, and brought back if it wanders off. "
                          "Off: the camera is yours (F)")
        speed = float(self.prefs.get("speed", 0.5))
        sp = W.TabRow(ctl, None, SPEEDS, speed, on_change=self.set_speed,
                      style="teal", bg=PANEL, small=True)
        sp.pack(side="left", padx=(SP_M, 0))
        self.tooltip.bind(sp, "Speed after a jump (and now, if a replay "
                          "is open)")

    def _remote_key(self):
        pb = self.playback
        return (pb is not None, bool(pb and pb.get("paused")),
                self._follow_on())

    def _follow_on(self) -> bool:
        return bool(self.prefs.get("follow", True))

    def _me(self):
        g = self.game
        return (g.me.champion, g.me.riot_id.split("#")[0]) if g else None

    def _check_replay(self):
        g = self.game
        if g is None:
            return
        gid, match = g.game_id, g.match_id

        def work():
            res = {}
            try:
                md = self.lcu.metadata(gid)
                state = md.get("state")
                if not md:          # client hasn't seen this game yet
                    try:
                        self.lcu.create_metadata(gid, self.cache.get_match(
                            match))
                        md = self.lcu.metadata(gid)
                        state = md.get("state")
                    except local.LocalError:
                        pass
                res = {"state": state, "text": local.REPLAY_STATE.get(
                    state, state or "Unknown"),
                    "progress": md.get("downloadProgress")}
                if state == "downloading" and md.get("downloadProgress"):
                    res["text"] = "Downloading… %d%%" % md["downloadProgress"]
            except local.NotRunning:
                f = self.rofls.get(match)
                if f is not None:
                    res = {"state": "offline", "text": "Replay on disk · "
                           "start the League client to watch"}
                else:
                    res = {"state": "offline",
                           "text": "Start the League client to download"}
            except local.LocalError as e:
                res = {"state": "error", "text": "Client: %s" % e}
            self.post(done, res)

        def done(res):
            if self.game is None or self.game.game_id != gid:
                return
            self.replay_state = res
            self._render_remote()
            if res.get("state") in ("downloading", "checking", "found"):
                self.root.after(1500, self._check_replay)
            elif res.get("state") == "watch" and match not in self.rofls:
                self.reload_games(then=lambda: None)
        threading.Thread(target=work, daemon=True).start()

    def download(self):
        g = self.game
        if g is None:
            return
        self._bg(lambda: self.lcu.download(g.game_id), "Downloading replay…",
                 then=lambda: self.root.after(800, self._check_replay))

    def watch(self):
        g = self.game
        if g is None:
            return
        if local.replay_api_enabled(self.install) is False:
            local.enable_replay_api(self.install)
        self._bg(lambda: self.lcu.watch(g.game_id),
                 "Starting the replay… click a moment to jump to it")

    def _bg(self, fn, msg, then=None):
        self.set_status(msg)

        def work():
            try:
                fn()
                if then:
                    self.post(then)
            except local.LocalError as e:
                self.post(self.set_status, str(e), BAD)
        threading.Thread(target=work, daemon=True).start()

    # replay control
    def _replay_call(self, fn, quiet=False):
        def work():
            try:
                fn()
            except local.LocalError:
                if not quiet:
                    self.post(lambda: self.set_status(
                        "No replay open — press Watch first", EDGE_HI))
        threading.Thread(target=work, daemon=True).start()

    def jump(self, t_ms: float):
        lead = float(self.prefs.get("lead_s", 15))
        offset = float(self.prefs.get("offset_s", 0))
        speed = float(self.prefs.get("speed", 0.5))
        target = max(t_ms / 1000 - lead + offset, 0)
        me = self._me() if self._follow_on() else None

        def go():
            self.rapi.set_playback(time=target, paused=False, speed=speed)
            if me:      # a seek clears the lock: lock once it's done
                self.rapi.follow_after_seek(*me)
        self._replay_call(go)
        self.set_status("Jumped to %s (%d s before)" % (fmt_time(
            t_ms / 1000), lead))
        if self.tl:
            self.tl.set_cursor(t_ms)

    def nudge(self, s):
        me = self._me() if self._follow_on() else None

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
        """Toggle "camera stays on me". Turning it on locks right away."""
        g = self.game
        if g is None:
            return
        on = not self._follow_on()
        self.prefs.set("follow", on)
        self._offscreen = 0
        self._render_remote()
        if not on:
            self.set_status("Camera is yours — Follow me is off", DIM)
            return

        def go():
            r = self.rapi.follow(g.me.champion, g.me.riot_id.split("#")[0])
            msg = {"locked": ("Camera locked on %s" % g.me.champion, GOOD),
                   "attached": ("Camera follows the selected unit — click "
                                "%s once in the replay" % g.me.champion,
                                EDGE_HI),
                   "failed": ("No replay open — press Watch first",
                              EDGE_HI)}[r]
            self.post(lambda: self.set_status(*msg))
        self._replay_call(go)

    def _poll_local(self):
        """Every second: is the client up, is a replay open, where is it."""
        me = self._me() if self._follow_on() else None

        def work():
            ok = self.lcu.running()
            try:
                pb = self.rapi.playback()
            except local.LocalError:
                pb = None
            if pb is not None and me and not pb.get("seeking"):
                self._keep_on_me(me)
            self.post(done, ok, pb)

        def done(ok, pb):
            was_ok = self.client_ok
            before = self._remote_key()
            self.client_ok, self.playback = ok, pb
            self._pb_at = time.monotonic()
            self._banner()
            if ok != was_ok and self.game is not None:
                self._check_replay()
            if self._remote_key() != before:
                self._render_remote()
            if self.tl and pb is not None:
                offset = float(self.prefs.get("offset_s", 0))
                self.tl.set_playhead((pb.get("time", 0) - offset) * 1000)
            elif self.tl:
                self.tl.set_playhead(None)
            if self._alive:
                self.root.after(1000, self._poll_local)
        threading.Thread(target=work, daemon=True).start()

    def _keep_on_me(self, me):
        """Worker thread, once a second while Follow me is on: if you're
        alive and off screen twice in a row (single off-screen frames
        happen while following), lock the camera on you again."""
        try:
            vis = self.rapi.on_screen(*me)
            self._offscreen = self._offscreen + 1 if vis is False else 0
            # the camera pans to you after a lock (up to ~8 s); re-locking
            # during the pan restarts it, so wait before trying again
            if self._offscreen >= 3 and                     time.monotonic() - self.rapi.last_lock > 9:
                self._offscreen = 0
                self.rapi.follow(*me)
        except local.LocalError:
            self._offscreen = 0

    # timeline
    def _timeline_panel(self, body):
        p, self._page["tl_right"] = panel(body, "TIMELINE", pady=(SP_M, 0),
                                          right="")
        self.tl = TimelineBar(p, self.tooltip, on_pick=self.pick_mark,
                              on_seek=self.seek_cursor)
        self.tl.pack(fill="x")
        leg = tk.Frame(p, bg=PANEL)
        leg.pack(fill="x", pady=(SP_S, 0))
        for text, col in (("✕ death", BAD), ("▲ missed chance", AMBER),
                          ("■ bookmark", FG), ("● kill / assist", GOOD),
                          ("◆ objective (gold = yours)", ACCENT),
                          ("• = untagged", EDGE_HI)):
            tk.Label(leg, text=text, bg=PANEL, fg=col, font=F_XS).pack(
                side="left", padx=(0, SP_L))
        add = tk.Frame(p, bg=PANEL)
        add.pack(fill="x", pady=(SP_S, 0))
        tk.Frame(add, bg=PANEL).pack(side="left", fill="x", expand=True)
        tk.Label(add, text="MARK AT REPLAY TIME", bg=PANEL, fg=DIM,
                 font=F_XS_B).pack(side="left", padx=(0, SP_S))
        b1 = W.button(add, "▲ Missed chance  M", lambda: self.add_mark(
            MISSED), font=F_SM, fg=AMBER)
        b1.pack(side="left", padx=(0, px(3)))
        b2 = W.button(add, "■ Bookmark  B", lambda: self.add_mark(BOOKMARK),
                      font=F_SM)
        b2.pack(side="left")
        self.tooltip.bind(b1, "Adds a mark where the replay is now (or "
                          "where you last clicked the timeline, if no "
                          "replay is open). Then pick what you missed.")

    def _fill_timeline(self, g: GameView):
        self._page["tl_right"].configure(text=(
            "click a moment to jump there · click the line to scrub"
            if g.has_timeline else "Riot has no timeline for this game"))
        self.tl.cursor_ms = None
        self.tl.set(g.duration_s * 1000, g.moments, self.marks,
                    self.sel_mark)

    def seek_cursor(self, ms):
        if self.tl:
            self.tl.set_cursor(ms)
        if self.playback is not None:
            self.jump(ms + float(self.prefs.get("lead_s", 15)) * 1000)

    def _now_ms(self) -> float | None:
        """Where the replay is now: the last poll (≤ 1 s old) moved on by
        the time since, at the replay's speed — no network call on the UI
        thread."""
        pb = self.playback
        if pb is not None:
            t = pb.get("time", 0.0)
            if not pb.get("paused"):
                t += (time.monotonic() - getattr(self, "_pb_at",
                                                 time.monotonic())) \
                    * float(pb.get("speed") or 1.0)
            offset = float(self.prefs.get("offset_s", 0))
            return (t - offset) * 1000
        if self.tl and self.tl.cursor_ms is not None:
            return self.tl.cursor_ms
        return None

    def add_mark(self, kind):
        g = self.game
        if g is None:
            return
        t = self._now_ms()
        if t is None:
            self.set_status("Open the replay (Watch) or click the timeline "
                            "where it happened first", EDGE_HI)
            return
        mk = self.notes.add_mark(g.match_id, g.puuid, kind,
                                 int(min(max(t, 0), g.duration_s * 1000)),
                                 g.me.champion)
        self.marks = self.notes.marks(g.match_id, g.puuid)
        self.sel_mark = mk.id
        self._after_marks_change()

    def _after_marks_change(self):
        if self.tl:
            self.tl.set(self.game.duration_s * 1000, self.game.moments,
                        self.marks, self.sel_mark)
        self._render_moments()
        self._render_tag()
        self._fill_games()

    def pick_mark(self, mark_id, jump=True):
        self.sel_mark = mark_id
        if self.tl:
            self.tl.select(mark_id)
        self._render_moments()
        self._render_tag()
        mk = self._mark(mark_id)
        if mk and jump and self.playback is not None:
            self.jump(mk.t_ms)

    def _mark(self, mark_id) -> Mark | None:
        return next((m for m in self.marks if m.id == mark_id), None)

    # moments list
    def _render_moments(self):
        """Moments list. Built once per game; picking/tagging only redraws
        the canvas (no widgets created or destroyed)."""
        box = getattr(self, "moments_box", None)
        if box is None or not box.winfo_exists():
            return
        n_tag = sum(1 for m in self.marks if m.tagged)
        count = "%d/%d tagged" % (n_tag, len(self.marks))
        lst = getattr(self, "_moment_list", None)
        if self.marks and lst is not None and lst.winfo_exists()                 and lst.master is box:
            self._moments_count.configure(text=count)
            lst.set(self._moment_rows(), self.sel_mark)
            return
        _clear(box)
        self._moment_list = None
        self._moments_count = W.section_title(box, "MOMENTS", EDGE_HI,
                                              right=count)
        if not self.marks:
            tk.Label(box, text="No deaths this game. Add missed chances or "
                     "bookmarks from the timeline.", bg=PANEL, fg=DIM,
                     font=F_SM, wraplength=px(300), justify="left").pack(
                anchor="w")
            return
        lst = self._moment_list = MomentList(box, self.pick_mark)
        lst.pack(fill="x")
        lst.set(self._moment_rows(), self.sel_mark)
        nxt = W.button(box, "Next untagged  N", self.next_untagged,
                       font=F_SM, fg=DIM)
        nxt.pack(anchor="e", pady=(SP_M, 0))

    def _moment_rows(self) -> list[MomentRow]:
        rows = []
        for mk in self.marks:
            sym = {DEATH: "✕", MISSED: "▲", BOOKMARK: "■"}[mk.kind]
            if mk.kind == DEATH:
                mo = self._death_moment(mk)
                lab = mo.label if mo else "Death"
            else:
                lab = KINDS[mk.kind] + ((" · " + mk.note.splitlines()[0][:30])
                                        if mk.note else "")
            cat = category_label(mk.kind, mk.category)
            chip = cat if cat else ("untagged" if mk.kind != BOOKMARK else "")
            rows.append(MomentRow(mk.id, sym, MARK_COLOR[mk.kind],
                                  fmt_time(mk.t_ms / 1000), lab, chip,
                                  GOOD if cat else EDGE_HI, not cat))
        return rows

    def _death_moment(self, mk: Mark):
        if self.game is None:
            return None
        return next((d for d in self.game.deaths_list if d.t_ms == mk.t_ms),
                    None)

    def next_untagged(self):
        if not self.marks:
            return
        ids = [m.id for m in self.marks]
        start = ids.index(self.sel_mark) + 1 if self.sel_mark in ids else 0
        order = self.marks[start:] + self.marks[:start]
        nxt = next((m for m in order if not m.tagged), None)
        if nxt:
            self.pick_mark(nxt.id)
        else:
            self.set_status("Everything in this game is tagged", GOOD)

    # tag editor
    def _render_tag(self):
        """The tag editor for the selected mark. Built once per game page,
        then only updated: rebuilding its ~30 widgets on every pick took
        150-250 ms on Windows and made picking moments feel sticky."""
        box = getattr(self, "tag_box", None)
        if box is None or not box.winfo_exists():
            return
        self._flush_note()
        ed = getattr(self, "_tag_ed", None)
        if ed is None or ed["box"] is not box or not box.winfo_exists():
            ed = self._tag_ed = self._build_tag_editor(box)
        mk = self._mark(self.sel_mark) if self.sel_mark else None
        if mk is None:
            ed["title"].configure(text="TAG", fg=EDGE_HI)
            ed["rule"].configure(bg=EDGE_HI)
            ed["right"].configure(text="")
            ed["body"].pack_forget()
            ed["empty"].pack(anchor="w")
            self._note_widget = None
            return
        ed["empty"].pack_forget()
        ed["body"].pack(fill="x")
        color = MARK_COLOR[mk.kind]
        ed["title"].configure(text=KINDS[mk.kind].upper() + "  ·  " +
                              fmt_time(mk.t_ms / 1000), fg=color)
        ed["rule"].configure(bg=color)
        ed["right"].configure(text="saved automatically")
        mo = self._death_moment(mk) if mk.kind == DEATH else None
        if mo is not None:
            ed["head"].configure(text=mo.label, fg=FG, font=F_SM_B,
                                 wraplength=0)
            ed["ctx"].configure(text="\n".join(
                "·  " + line for line in context_lines(mo)))
            ed["ctx"].pack(anchor="w", after=ed["head"])
        else:
            ed["head"].configure(
                text="Your mark at %s." % fmt_time(mk.t_ms / 1000)
                + (" Pick what you missed below." if mk.kind == MISSED
                   else ""), fg=DIM, font=F_SM, wraplength=px(320))
            ed["ctx"].pack_forget()
        if mk.kind != DEATH:
            ed["delete"].pack(side="left", padx=(SP_S, 0))
        else:
            ed["delete"].pack_forget()
        if mo is not None and mo.x is not None:
            ed["map"].pack(side="right", anchor="n")
            ed["map"].mark(mo.x, mo.y)
        else:
            ed["map"].pack_forget()
        for kind in (DEATH, MISSED):
            ch = ed["chips"][kind]
            if kind == mk.kind:
                ch.set(mk.category)
                ch.grid()
            else:
                ch.grid_remove()
        if mk.kind in (DEATH, MISSED):
            ed["why"].configure(text="WHY" if mk.kind == DEATH
                                else "WHAT YOU MISSED")
            ed["why"].grid()
        else:
            ed["why"].grid_remove()
        for w in (ed["avoid_cap"], ed["avoid"]):
            if mk.kind == DEATH:
                w.grid()
            else:
                w.grid_remove()
        ed["avoid"].set(mk.avoidable)
        t = ed["note"]
        t.delete("1.0", "end")
        t.insert("1.0", mk.note or "")
        t.edit_reset()                       # no undo into the last mark
        self._note_widget, self._note_id = t, mk.id

    def _build_tag_editor(self, box) -> dict:
        _clear(box)
        ed = {"box": box}
        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x")
        ed["title"] = tk.Label(row, text="TAG", bg=PANEL, fg=EDGE_HI,
                               font=F_SM_B)
        ed["title"].pack(side="left")
        ed["right"] = tk.Label(row, text="", bg=PANEL, fg=DIM, font=F_XS)
        ed["right"].pack(side="right")
        ed["rule"] = tk.Frame(box, bg=EDGE_HI, height=2)
        ed["rule"].pack(fill="x", pady=(SP_XS, SP_M))
        ed["empty"] = tk.Label(box, text="Pick a moment on the left or on "
                               "the timeline.", bg=PANEL, fg=DIM, font=F_SM)
        body = ed["body"] = tk.Frame(box, bg=PANEL)
        body.grid_columnconfigure(0, weight=1)
        top = tk.Frame(body, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew")
        info = tk.Frame(top, bg=PANEL)
        info.pack(side="left", fill="both", expand=True)
        ed["head"] = tk.Label(info, text="", bg=PANEL, fg=FG, font=F_SM_B,
                              justify="left")
        ed["head"].pack(anchor="w")
        ed["ctx"] = tk.Label(info, text="", bg=PANEL, fg=DIM, font=F_SM,
                             justify="left")
        acts = tk.Frame(info, bg=PANEL)
        acts.pack(anchor="w", side="bottom", pady=(SP_M, 0))
        W.button(acts, "▶  Jump in replay", lambda: (
            self._mark(self.sel_mark) and self.jump(
                self._mark(self.sel_mark).t_ms)), gold=True).pack(side="left")
        ed["delete"] = W.button(acts, "Delete", lambda: self.sel_mark
                                and self.delete_mark(self.sel_mark),
                                font=F_SM, fg=DIM)
        ed["map"] = MiniMap(top, self.icons.minimap(), size=140)

        def cap(r, text=""):
            lbl = tk.Label(body, text=text, bg=PANEL, fg=DIM, font=F_XS_B)
            lbl.grid(row=r, column=0, sticky="w", pady=(SP_L, SP_XS))
            return lbl
        ed["why"] = cap(1)
        ed["chips"] = {}
        for r, kind in ((2, DEATH), (3, MISSED)):
            cats = CATEGORIES[kind]
            # full rows: 9 reasons as 3x3, otherwise rows of 4
            cols = 3 if len(cats) % 4 and not len(cats) % 3 else 4
            ch = Chips(body, cats, None, columns=cols, color=GOOD,
                       on_change=lambda v: self.sel_mark and self._set(
                           self.sel_mark, category=v))
            ch.grid(row=r, column=0, sticky="ew")
            ed["chips"][kind] = ch
        ed["avoid_cap"] = cap(4, "COULD YOU HAVE AVOIDED IT?")
        ed["avoid"] = Chips(body, AVOIDABLE, None, columns=6, color=EDGE_HI,
                            active_bg=mix(PANEL2, ACCENT, 0.18),
                            on_change=lambda v: self.sel_mark and self._set(
                                self.sel_mark, avoidable=v))
        ed["avoid"].grid(row=5, column=0, sticky="w")
        cap(6, "NOTE — WHAT SHOULD YOU HAVE DONE?")
        t = ed["note"] = text_box(body, height=3)
        t.grid(row=7, column=0, sticky="ew")
        t.bind("<KeyRelease>", lambda e: self._queue_note())
        t.bind("<FocusOut>", lambda e: self._flush_note())
        return ed

    def _set(self, mark_id, **fields):
        self.notes.update_mark(mark_id, **fields)
        mk = self._mark(mark_id)
        if mk:
            for k, v in fields.items():
                setattr(mk, k, v)
        if self.tl:
            self.tl.set(self.game.duration_s * 1000, self.game.moments,
                        self.marks, self.sel_mark)
        self._render_moments()
        self._fill_games()

    def _queue_note(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(600, self._flush_note)

    def _flush_note(self):
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self._save_job = None
        t = getattr(self, "_note_widget", None)
        if t is None:
            return
        try:
            text = t.get("1.0", "end").strip()
        except tk.TclError:
            self._note_widget = None
            return
        mk = self._mark(self._note_id)
        if mk and text != (mk.note or ""):
            mk.note = text
            self.notes.update_mark(mk.id, note=text)
            if mk.kind != DEATH:
                self._render_moments()

    def delete_mark(self, mark_id):
        self._note_widget = None
        self.notes.delete_mark(mark_id)
        g = self.game
        self.marks = self.notes.marks(g.match_id, g.puuid)
        self.sel_mark = self.marks[0].id if self.marks else None
        self._after_marks_change()

    # matchup
    def _matchup_panel(self, body):
        pg = self._page
        p, pg["mu_right"] = panel(body, "MATCHUP", GOOD, pady=(SP_M, 0),
                                  right="")
        cols = tk.Frame(p, bg=PANEL)
        cols.pack(fill="x")
        cols.grid_columnconfigure(0, weight=3, uniform="m")
        cols.grid_columnconfigure(1, weight=2, uniform="m")
        left = tk.Frame(cols, bg=PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, SP_L))
        pg["mu_hist"] = tk.Frame(cols, bg=PANEL)
        pg["mu_hist"].grid(row=0, column=1, sticky="nsew")

        def save(**kw):
            mu = pg.get("mu")
            if mu is None:
                return
            for k, v in kw.items():
                setattr(mu, k, v)
            self.notes.save_matchup(mu)
            if "reviewed" in kw:
                self._fill_games()
        W.caption(left, "HOW DID THE LANE GO?", pady=(0, SP_XS))
        pg["mu_lane"] = Chips(left, LANE_RATING, None, columns=5,
                              on_change=lambda v: save(lane=v))
        pg["mu_lane"].pack(fill="x")
        W.caption(left, "WHAT WORKED, WHAT TO DO NEXT TIME")
        t = pg["mu_note"] = text_box(left, height=4)
        t.pack(fill="x")
        job = {"id": None}

        def flush():
            if job["id"]:
                try:
                    self.root.after_cancel(job["id"])
                except tk.TclError:
                    pass
            job["id"] = None
            mu = pg.get("mu")
            try:
                txt = t.get("1.0", "end").strip()
            except tk.TclError:
                return
            if mu is not None and txt != (mu.note or ""):
                save(note=txt)

        def queue(_):
            if job["id"]:
                self.root.after_cancel(job["id"])
            job["id"] = self.root.after(600, flush)
        pg["mu_flush"] = flush
        t.bind("<KeyRelease>", queue)
        t.bind("<FocusOut>", lambda e: flush())
        done = tk.Frame(left, bg=PANEL)
        done.pack(anchor="w", pady=(SP_M, 0))
        pg["mu_rev"] = Chips(done, [(True, "✓  Game reviewed")], None,
                             columns=1, color=GOOD,
                             on_change=lambda v: save(reviewed=bool(v)))
        pg["mu_rev"].pack(side="left")

    def _fill_matchup(self, g: GameView):
        pg = self._page
        if pg.get("mu") is not None:
            pg["mu_flush"]()              # a note typed for the last game
        mu = pg["mu"] = self.notes.matchup(g.match_id, g.puuid) or Matchup(
            g.match_id, g.puuid)
        lane = [static.champion_name(g.me.champion)]
        if g.ally_support:
            lane.append(static.champion_name(g.ally_support.champion))
        foe = [static.champion_name(x.champion) for x in
               (g.opponent, g.enemy_support) if x]
        pg["mu_right"].configure(text="%s  vs  %s" % (
            " + ".join(lane), " + ".join(foe) or "?"))
        pg["mu_lane"].set(mu.lane)
        t = pg["mu_note"]
        t.delete("1.0", "end")
        t.insert("1.0", mu.note or "")
        t.edit_reset()
        pg["mu_rev"].set(True if mu.reviewed else None)
        # earlier notes against this opponent: small and varies, rebuilt
        right = pg["mu_hist"]
        _clear(right)
        opp = g.opponent.champion if g.opponent else None
        hist = self.notes.matchup_history(None, opp, exclude_match=g.match_id,
                                          puuids=[g.puuid]) if opp else []
        W.caption(right, "EARLIER VS %s" % (static.champion_name(opp).upper()
                                           if opp else "?"),
                  pady=(0, SP_XS))
        if not hist:
            tk.Label(right, text="No earlier notes against this champion "
                     "yet. They'll show up here (and in Draft) next time.",
                     bg=PANEL, fg=DIM, font=F_SM, wraplength=px(320),
                     justify="left").pack(anchor="w")
        for h in hist[:4]:
            fr = tk.Frame(right, bg=PANEL2, padx=px(8), pady=px(4))
            fr.pack(fill="x", pady=(0, px(4)))
            lane_txt = dict(LANE_RATING).get(h.lane, "")
            tk.Label(fr, text="%s%s" % (static.champion_name(h.champion or ""),
                                        ("  ·  " + lane_txt) if lane_txt
                                        else ""),
                     bg=PANEL2, fg=EDGE_HI, font=F_XS_B).pack(anchor="w")
            if h.note:
                tk.Label(fr, text=h.note, bg=PANEL2, fg=FG, font=F_SM,
                         wraplength=px(320), justify="left").pack(anchor="w")
            fr.bind("<Button-1>", lambda e, m=h.match_id: self.open_game(m))

    # ==================================================== INSIGHTS view
    def _view_insights(self):
        sa = W.ScrollArea(self.content)
        sa.pack(fill="both", expand=True)
        body = sa.body
        if not self.player:
            p, _ = panel(body, "INSIGHTS")
            tk.Label(p, text="Pick an account first.", bg=PANEL, fg=DIM,
                     font=F_SM).pack(anchor="w")
            return
        puuid = self.player["puuid"]
        champs = sorted({g.champion for g in self.games})
        champ = getattr(self, "_ins_champ", None)
        if champ not in champs:
            champ = None
        tagged, total, games = self.notes.tagged_counts(puuid)
        p, _ = panel(body, "YOUR REVIEWS", right="only tagged moments count")
        strip = tk.Frame(p, bg=PANEL)
        strip.pack(fill="x")
        reviewed = len(self.notes.reviewed_games(puuid))
        d_counts = self.notes.category_counts(puuid, DEATH, champ)
        m_counts = self.notes.category_counts(puuid, MISSED, champ)
        n_d = sum(c for c, _ in d_counts.values())
        n_av = sum(a for _, a in d_counts.values())
        items = [("GAMES REVIEWED", "%d" % reviewed, FG,
                  "marked “Game reviewed”"),
                 ("DEATHS TAGGED", "%d / %d" % (tagged, total), FG,
                  "in %d games" % games),
                 ("AVOIDABLE", ("%.0f%%" % (100 * n_av / n_d)) if n_d
                  else "—", BAD if n_d and n_av / n_d > 0.5 else FG,
                  "of tagged deaths"),
                 ("MISSED CHANCES", "%d" % sum(c for c, _ in
                                               m_counts.values()), AMBER,
                  "marked by you")]
        for i, (lab, val, col, sub) in enumerate(items):
            f = tk.Frame(strip, bg=PANEL)
            f.pack(side="left", padx=(0 if i == 0 else px(34), 0))
            tk.Label(f, text=lab, bg=PANEL, fg=DIM, font=F_XS_B).pack(
                anchor="w")
            tk.Label(f, text=val, bg=PANEL, fg=col, font=F_LG_B).pack(
                anchor="w")
            tk.Label(f, text=sub, bg=PANEL, fg=DIM, font=F_XS).pack(
                anchor="w")
        flt = tk.Frame(body, bg=DARK_BG)
        flt.pack(fill="x", pady=(SP_M, SP_S))
        W.TabRow(flt, "CHAMPION", [(None, "All")] + [(c, c) for c in champs
                                                     [:10]], champ,
                 on_change=self._ins_pick, style="teal").pack(side="left")
        row = tk.Frame(body, bg=DARK_BG)
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1, uniform="i")
        row.grid_columnconfigure(1, weight=1, uniform="i")
        for col, (title, color, cats, counts, avoid) in enumerate((
                ("WHY YOU DIE", BAD, DEATH_CATEGORIES, d_counts, True),
                ("WHAT YOU MISS", AMBER, MISSED_CATEGORIES, m_counts,
                 False))):
            cp = W.CutPanel(row, padx=14, fill=True)
            cp.grid(row=0, column=col, sticky="nsew",
                    padx=(0, SP_M) if col == 0 else 0)
            n = sum(c for c, _ in counts.values())
            W.section_title(cp.inner, title, color, right="n=%d" % n)
            self._bars(cp.inner, cats, counts, color, avoid, n)
        # the one thing to work on
        if n_d >= 5:
            top = max(d_counts.items(), key=lambda kv: kv[1][0])
            if top[0] != "worth":
                p, _ = panel(body, "FOCUS NEXT", EDGE_HI, pady=(SP_M, 0))
                lbl = tk.Label(p, text="%s is behind %.0f%% of your tagged deaths%s."
                         " Make it the one thing you watch for next session."
                         % (category_label(DEATH, top[0]),
                            100 * top[1][0] / n_d,
                            " (%d of them avoidable)" % top[1][1]
                            if top[1][1] else ""),
                         bg=PANEL, fg=FG, font=F_MD, wraplength=px(900),
                         justify="left")
                lbl.pack(anchor="w", fill="x")
                lbl.bind("<Configure>", lambda e, l=lbl: l.configure(
                    wraplength=max(e.width - px(4), px(200))))
        # recent notes
        p, _ = panel(body, "RECENT DEATH NOTES", pady=(SP_M, 0))
        notes = [m for m in self.notes.recent_marks(puuid, DEATH, 40)
                 if m.note and (champ is None or m.champion == champ)][:12]
        if not notes:
            tk.Label(p, text="Notes you write on deaths show up here.",
                     bg=PANEL, fg=DIM, font=F_SM).pack(anchor="w")
        for m in notes:
            r = tk.Frame(p, bg=PANEL, cursor="hand2")
            r.pack(fill="x", pady=1)
            tk.Label(r, text="%s  %s" % (m.champion or "", fmt_time(
                m.t_ms / 1000)), bg=PANEL, fg=DIM, font=F_XS, width=18,
                anchor="w").pack(side="left")
            tk.Label(r, text=category_label(DEATH, m.category), bg=PANEL,
                     fg=GOOD, font=F_XS_B, width=22, anchor="w").pack(
                side="left")
            tk.Label(r, text=m.note, bg=PANEL, fg=FG, font=F_SM,
                     anchor="w", justify="left", wraplength=px(700)).pack(
                side="left", fill="x", expand=True)
            for w in (r, *r.winfo_children()):
                w.bind("<Button-1>", lambda e, mm=m: self._open_mark(mm))

    def _ins_pick(self, c):
        self._ins_champ = c
        self.navigate("insights")

    def _open_mark(self, m: Mark):
        self.sel_mark = m.id
        self.navigate("review")
        self.open_game(m.match_id, keep_sel=True)

    def _bars(self, parent, cats, counts, color, avoid, n):
        if not n:
            tk.Label(parent, text="Nothing tagged yet.", bg=PANEL, fg=DIM,
                     font=F_SM).pack(anchor="w")
            return
        for key, lab in sorted(cats, key=lambda kv: -counts.get(kv[0],
                                                                (0, 0))[0]):
            c, av = counts.get(key, (0, 0))
            r = tk.Frame(parent, bg=PANEL)
            r.pack(fill="x", pady=2)
            tk.Label(r, text=lab, bg=PANEL, fg=FG if c else DIM, font=F_SM,
                     width=22, anchor="w").pack(side="left")
            bar = tk.Canvas(r, bg=PANEL, highlightthickness=0,
                            height=px(12), width=1)
            bar.pack(side="left", fill="x", expand=True, padx=px(6))
            f, fa = c / n, (av / n if avoid else 0)

            def draw(e, bb=bar, f=f, fa=fa):
                bb.delete("all")
                bb.create_rectangle(0, 1, e.width, e.height - 1, fill=PANEL2,
                                    width=0)
                bb.create_rectangle(0, 1, e.width * f, e.height - 1,
                                    fill=mix(color, PANEL, 0.45), width=0)
                bb.create_rectangle(0, 1, e.width * fa, e.height - 1,
                                    fill=color, width=0)
            bar.bind("<Configure>", draw)
            txt = "%d  (%.0f%%)" % (c, 100 * f)
            tk.Label(r, text=txt, bg=PANEL, fg=DIM, font=F_XS, width=10,
                     anchor="e").pack(side="left")
        if avoid:
            tk.Label(parent, text="bright = you said it was avoidable",
                     bg=PANEL, fg=DIM, font=F_XS).pack(anchor="w",
                                                       pady=(SP_S, 0))

    # ==================================================== MATCHUPS view
    def _view_matchups(self):
        p = W.CutPanel(self.content, padx=14, grow=True)
        p.pack(fill="both", expand=True)
        if not self.player:
            W.section_title(p.inner, "MATCHUPS", GOOD)
            return
        rows = self.notes.all_matchups(self.player["puuid"])
        W.section_title(p.inner, "MATCHUP NOTES", GOOD,
                        right="%d notes · click to open the game" % len(rows))
        cols = [W.Col("icon", "", 0, kind="icon"),
                W.Col("opp", "VS", 10, "w", sort=lambda r: r.data.opponent
                      or ""),
                W.Col("me", "YOU", 10, "w", sort=lambda r: r.data.champion
                      or ""),
                W.Col("sup", "SUPPORTS", 16, "w"),
                W.Col("lane", "LANE", 9, "center",
                      sort=lambda r: r.data.lane or 0),
                W.Col("note", "NOTE", 30, "w", stretch=True),
                W.Col("date", "UPDATED", 10, "e",
                      sort=lambda r: r.data.updated or 0)]
        t = W.Table(p.inner, cols, icon_loader=self.icons.get,
                    on_click=lambda r: self.open_game(r.data.match_id),
                    rows_visible=None, tooltip=self.tooltip,
                    default_sort="date")
        t.pack(fill="both", expand=True)
        lane_col = {1: BAD, 2: mix(BAD, FG, 0.4), 3: FG, 4: mix(GOOD, FG, 0.4),
                    5: GOOD}
        out = []
        for m in rows:
            note = (m.note or "").replace("\n", " · ")
            out.append(W.Row({
                "opp": (static.champion_name(m.opponent or "?"), FG),
                "me": (static.champion_name(m.champion or "?"), DIM),
                "sup": ("%s vs %s" % (m.ally_support or "—",
                                      m.enemy_support or "—"), DIM),
                "lane": (dict(LANE_RATING).get(m.lane, "—"),
                         lane_col.get(m.lane, DIM)),
                "note": (note[:90] + ("…" if len(note) > 90 else ""), FG),
                "date": (datetime.fromtimestamp(m.updated).strftime(
                    "%d %b") if m.updated else "", DIM)},
                icon=m.opponent, data=m))
        t.set_rows(out)

    # ------------------------------------------------------ keyboard
    def _bind_keys(self):
        def guard(fn):
            def h(e):
                if isinstance(e.widget, (tk.Text, tk.Entry)):
                    return None
                fn()
                return "break"
            return h
        r = self.root
        r.bind("<space>", guard(self.toggle_pause))
        r.bind("<Left>", guard(lambda: self.nudge(-10)))
        r.bind("<Right>", guard(lambda: self.nudge(10)))
        for k in ("n", "N"):
            r.bind("<%s>" % k, guard(self.next_untagged))
        for k in ("m", "M"):
            r.bind("<%s>" % k, guard(lambda: self.add_mark(MISSED)))
        for k in ("b", "B"):
            r.bind("<%s>" % k, guard(lambda: self.add_mark(BOOKMARK)))
        for k in ("f", "F"):
            r.bind("<%s>" % k, guard(self.follow_me))
        for k in ("g", "G"):
            r.bind("<%s>" % k, guard(self.toggle_games))
        r.bind("<Escape>", guard(self.close_games_overlay))

    # ------------------------------------------------------ settings
    def show_settings(self):
        d = tk.Toplevel(self.root, bg=EDGE)
        d.title("Replay settings")
        d.transient(self.root)
        body = tk.Frame(d, bg=PANEL, padx=px(16), pady=px(12))
        body.pack(padx=1, pady=1)
        W.section_title(body, "SETTINGS", EDGE_HI)
        fields = {}

        def field(key, label, default, hint):
            W.caption(body, label)
            e = W.entry(body, 40)
            e.insert(0, str(self.prefs.get(key, default)))
            e.pack(anchor="w")
            tk.Label(body, text=hint, bg=PANEL, fg=DIM, font=F_XS,
                     wraplength=px(380), justify="left").pack(anchor="w")
            fields[key] = (e, type(default))
        field("lead_s", "SECONDS BEFORE A MOMENT", 15,
              "A jump starts this long before the death, so you see the "
              "lead-up.")
        field("offset_s", "CLOCK OFFSET (SECONDS)", 0.0,
              "If jumps land consistently early/late, put the difference "
              "here (replay time − game time).")
        field("fetch_count", "GAMES TO FETCH", 30,
              "How many recent ranked games “Fetch new games” checks.")
        field("league_dir", "LEAGUE FOLDER", str(self.install),
              "Where League is installed (has Config\\game.cfg and the "
              "client's lockfile).")
        field("replays_dir", "REPLAYS FOLDER", str(rofl.default_dir()),
              "Where the client saves .rofl files.")
        en = local.replay_api_enabled(self.install)
        W.caption(body, "REPLAY API")
        tk.Label(body, text={True: "On (EnableReplayApi=1 in game.cfg)",
                             False: "Off — will be switched on the first "
                             "time you press Watch",
                             None: "game.cfg not found — check the League "
                             "folder"}[en],
                 bg=PANEL, fg=GOOD if en else EDGE_HI, font=F_SM).pack(
            anchor="w")
        fol = tk.BooleanVar(value=bool(self.prefs.get("follow", True)))
        W.caption(body, "CAMERA")
        tk.Checkbutton(body, text="Follow me: keep the camera on me (also "
                       "the ◎ button, F)",
                       variable=fol, bg=PANEL, fg=FG, selectcolor=PANEL2,
                       activebackground=PANEL, activeforeground=FG,
                       font=F_SM).pack(anchor="w")

        def save():
            for k, (e, typ) in fields.items():
                v = e.get().strip()
                try:
                    self.prefs.set(k, typ(v) if typ is not str else v)
                except ValueError:
                    pass
            self.prefs.set("follow", bool(fol.get()))
            self.install = local.install_dir(self.prefs.get("league_dir"))
            self.lcu = local.LeagueClient(self.install)
            d.destroy()
            self.reload_games(then=lambda: None)
        W.button(body, "Save", save, gold=True).pack(anchor="e",
                                                    pady=(SP_L, 0))
        tk.Label(body, text=LEGAL, bg=PANEL, fg=mix(DIM, PANEL, 0.3),
                 font=F_XS, wraplength=px(380), justify="left").pack(
            anchor="w", pady=(SP_L, 0))

    # ------------------------------------------------------ lifecycle
    def _log(self, msg):
        try:
            with open(config.data_dir() / "replay.log", "a",
                      encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.ctime(), msg))
        except OSError:
            pass

    def close(self):
        try:
            self._flush_note()
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
