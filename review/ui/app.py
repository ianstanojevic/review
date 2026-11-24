"""REVIEW desktop app — DRAFT's window, REVIEW's content.

Layout (top to bottom): banner (title · Riot ID + rank · ⚙), VIEW tab row,
filter row (ROLE | QUEUE | RANGE | CHAMPION), the active screen, and a
bottom line with the status (right). Riot's legal notice is in Settings.

Threads: fetching (SyncJob) and analysis run off the UI thread; results
come back through ``post``. A generation counter drops stale analyses.
"""
from __future__ import annotations

import json
import threading
import time
import tkinter as tk
import traceback

from .. import config, static
from ..analysis import ROLE_LABEL, ROLES, Filter
from ..cache import Cache
from ..pipeline import analyze_player
from ..riot_client import (InvalidKey, MissingKey, PlayerNotFound,
                           RateLimited, ReviewError, ServiceUnavailable,
                           default_client, get_region, parse_riot_id)
from ..sync import QUEUE_LABEL, QUEUES, SyncJob
from ..theme import anim
from ..theme import widgets as W
from ..theme import window
from ..theme.tokens import (BAD, DARK_BG, DIM, EDGE, EDGE_HI, F_SM, F_SM_B,
                            F_XS, FG, GOOD, PANEL, PANEL2, SP_L, SP_M, SP_S,
                            SP_XS)
from ..theme.widgets import px
from .assets import IconStore

LEGAL = ("REVIEW isn't endorsed by Riot Games and doesn't reflect the views or "
         "opinions of Riot Games or anyone officially involved in producing or "
         "managing Riot Games properties. Riot Games, and all associated "
         "properties are trademarks or registered trademarks of Riot Games, "
         "Inc.")
VIEWS = [("overview", "Overview"), ("lane", "Lane"),
         ("champions", "Champions"), ("trends", "Trends"),
         ("winloss", "Wins vs losses"), ("deaths", "Deaths"),
         ("radar", "Radar"), ("compare", "Compare"), ("lab", "Stats lab"),
         ("matches", "Matches")]
VIEW_LABEL = dict(VIEWS)
RANGES = [(None, "All"), (100, "Last 100"), (50, "Last 50"), (20, "Last 20")]
PARTIAL_MIN = 20          # show results once this many games are cached
# … then refresh at 2x, 4x … that many games (20, 40, 80, 160, 320): each
# refresh rebuilds the open screen on the UI thread (~1 s), so a 500-game
# fetch must not trigger one every few games
TIER_LABEL = {"IRON": "Iron", "BRONZE": "Bronze", "SILVER": "Silver",
              "GOLD": "Gold", "PLATINUM": "Platinum", "EMERALD": "Emerald",
              "DIAMOND": "Diamond", "MASTER": "Master",
              "GRANDMASTER": "Grandmaster", "CHALLENGER": "Challenger"}


def _level_label(level) -> str:
    from ..benchmark import LEVELS
    return LEVELS[level].label if level in LEVELS else ""


def safe_destroy(w, log=None) -> None:
    """destroy() that can't be aborted halfway: if tkinter's bookkeeping
    of a callback is stale ("can't delete Tcl command"), destroy the Tk
    widget anyway so a screen switch / close always completes."""
    try:
        w.destroy()
    except tk.TclError as e:
        if log:
            log(e)
        def forget_cmds(x):              # drop the stale records in the
            x._tclCommands = None        # whole subtree (Misc.destroy
            for c in list(getattr(x, "children", {}).values()):
                forget_cmds(c)           # skips None)…
        try:
            forget_cmds(w)
            w.destroy()                  # …and finish the job
        except Exception:
            try:
                w.tk.call("destroy", w._w)
            except Exception:
                pass


_parse_geo = window.parse_geo


def rank_text(snap) -> str:
    if snap is None or not snap.tier:
        return "Unranked"
    apex = snap.tier in ("MASTER", "GRANDMASTER", "CHALLENGER")
    return "%s%s %d LP" % (TIER_LABEL.get(snap.tier, snap.tier),
                           "" if apex else " " + (snap.division or ""),
                           snap.lp or 0)


class Prefs:
    def __init__(self):
        self.path = config.data_dir() / "prefs.json"
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


class App:
    def __init__(self):
        self.root = root = W.make_root("Review")
        self.tooltip = W.Tooltip(root)
        W.install_wheel(root)
        self.icons = IconStore(root)
        from ..cache import open_cache
        self.cache, self._cache_reset = open_cache(config.db_path())
        self.prefs = Prefs()
        # from disk right away (no network on the UI thread); the
        # background loader refreshes it and re-analyses only on change
        try:
            self.legendary: frozenset[int] = static.legendary_ids(
                offline=True)
        except Exception:
            self.legendary = frozenset()
        self.player: dict | None = None     # puuid, riot_id, region
        self._clean_saved_ids()
        self.queue_key = self.prefs.get("queue", "solo")
        self.count = int(self.prefs.get("count", 150))
        self.role = self.champion = self.last_n = None
        self.reference = self.prefs.get("reference")   # None|diamond|master
        self.bench_job = None
        self.result = None
        self.view = self.prefs.get("view", "overview")
        if self.view not in VIEW_LABEL:
            self.view = "overview"
        self._synced_at: float | None = None   # last finished download
        self.job: SyncJob | None = None
        self._reveal_next = True     # next built screen gets the reveal
        self.gen = 0
        self._next_partial = PARTIAL_MIN
        self._analysing = 0          # analyses in flight
        self.screens: dict[str, tk.Frame] = {}
        self.dirty: set[str] = set()
        self._build()
        root.geometry(self._safe_geometry(self.prefs.get("geometry")))
        root.minsize(px(980), px(640))
        root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self._load_static, daemon=True).start()
        self._start_watchdog()
        # a windowed exe has no stderr: log every callback exception
        root.report_callback_exception = (
            lambda exc, val, tb: self._log(val))
        self._install_shortcuts()
        self.root.after(30000, self._tick_updated)
        self.show_search()
        if self._cache_reset:
            self._trace("cache damaged, set aside as %s" % self._cache_reset)
            self.set_status("Review's saved games were damaged and have been "
                            "reset — they'll download again.", BAD)
        anim.fade_in(root)

    # ------------------------------------------------------- window place
    # (shared with REPLAY: review/theme/window.py)
    _virtual_screen = staticmethod(window.virtual_screen)
    _work_area = staticmethod(window.work_area)

    def _on_screen(self, w, h, x, y) -> bool:
        return window.on_screen(w, h, x, y)

    def _safe_geometry(self, saved: str | None) -> str:
        return window.safe_geometry(self.root, saved, (1240, 900))

    # ------------------------------------------------------------ layout
    def _build(self):
        outer = tk.Frame(self.root, bg=DARK_BG, padx=14, pady=12)   # = DRAFT
        outer.pack(fill="both", expand=True)
        self.banner = W.Banner(outer, "REVIEW", on_gear=self.show_settings)
        self.banner.pack(fill="x")

        self.bars = tk.Frame(outer, bg=DARK_BG)
        nav = tk.Frame(self.bars, bg=DARK_BG)
        nav.pack(fill="x", pady=(SP_L, SP_XS))
        self.nav = W.TabRow(nav, "VIEW", VIEWS, self.view,
                            on_change=self.navigate)
        self.nav.pack(side="left")
        ns = W.button(nav, "New search", self.show_search, fg=DIM, font=F_SM)
        ns.pack(side="right")
        rf = W.button(nav, "↻ Refresh", self.refresh, fg=DIM, font=F_SM)
        rf.pack(side="right", padx=(0, SP_S))
        cp = W.button(nav, "Copy view", self.copy_view, fg=DIM, font=F_SM)
        cp.pack(side="right", padx=(0, SP_S))
        for b, tip in ((rf, "Fetch new games for this player  (F5)"),
                       (cp, "Copy what you see as a picture, ready to paste "
                        "into Discord  (Ctrl+Shift+C)")):
            b.bind("<Enter>", lambda e, t=tip: self.tooltip.show(
                e.x_root, e.y_root, t), add="+")
            b.bind("<Leave>", lambda e: self.tooltip.hide(), add="+")
        flt = tk.Frame(self.bars, bg=DARK_BG)
        flt.pack(fill="x", pady=(SP_XS, SP_M))
        from ..theme.roleicons import role_photo_alpha
        from ..theme.tokens import BTN_FG
        isz = px(14)
        self.role_row = W.TabRow(
            flt, "ROLE", [(None, "All")] + [(r, ROLE_LABEL[r]) for r in ROLES],
            None, on_change=self._set_role,
            icons={r: (role_photo_alpha(r, isz, DIM),
                       role_photo_alpha(r, isz, BTN_FG)) for r in ROLES})
        self.role_row.pack(side="left")
        W.vsep(flt)
        self.queue_row = W.TabRow(flt, "QUEUE", [("solo", "Solo/Duo"),
                                                 ("flex", "Flex"),
                                                 ("both", "Both")],
                                  self.queue_key, on_change=self._set_queue,
                                  style="teal")
        self.queue_row.pack(side="left")
        W.vsep(flt)
        self.range_row = W.TabRow(flt, "RANGE", RANGES, None,
                                  on_change=self._set_range, style="teal")
        self.range_row.pack(side="left")
        flt2 = tk.Frame(self.bars, bg=DARK_BG)
        flt2.pack(fill="x", pady=(0, SP_M))
        self.champ_row = W.TabRow(flt2, "CHAMPION", [(None, "All")], None,
                                  on_change=self._set_champion, style="teal")
        self.champ_row.pack(side="left")
        # what "50" means: the players in your own games, or a fixed
        # benchmark of higher-ranked games shared by every account
        self.vs_row = W.TabRow(flt2, "VS", [(None, "My games"),
                                            ("gold", "Gold"),
                                            ("platinum", "Plat"),
                                            ("emerald", "Emerald"),
                                            ("diamond", "Diamond"),
                                            ("master", "Master+")],
                               self.reference, on_change=self._set_reference,
                               style="teal")
        self.vs_row.pack(side="right")
        self.tooltip.bind(self.vs_row, "What 50 means. My games: the "
                          "same-role players in this account's own games. "
                          "Diamond / Master+: a fixed benchmark of real "
                          "games at that level, so scores compare across "
                          "accounts.")

        # every view lives in ONE grid cell; switching raises it instead of
        # re-packing (re-packing re-lays out the whole tree: ~150 ms)
        self.content = tk.Frame(outer, bg=DARK_BG)
        self.content.pack(fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        bottom = tk.Frame(outer, bg=DARK_BG)
        bottom.pack(fill="x", side="bottom", pady=(SP_M, 0))
        self.status = tk.Label(bottom, text="", bg=DARK_BG, fg=DIM, font=F_SM)
        self.status.pack(side="right", padx=(SP_M, SP_XS), anchor="s")
        self.bench_status = tk.Label(bottom, text="", bg=DARK_BG, fg=DIM,
                                     font=F_SM)
        self.bench_status.pack(side="right", padx=(SP_M, 0), anchor="s")
        # Riot's legal notice lives in Settings (⚙), not on every page

    def _load_static(self):
        try:
            leg = static.legendary_ids()
            static.refresh_champions()
            static.minimap()
        except Exception:
            leg = frozenset()

        def done():
            if not leg:            # offline: keep what we loaded from disk
                return
            changed = leg != self.legendary
            self.legendary = leg
            if changed and self.player:
                self.reanalyze()
        self.post(done)

    def post(self, fn, *args):
        try:
            self.root.after(0, fn, *args)
        except (RuntimeError, tk.TclError):
            pass

    def set_status(self, text: str, color: str = DIM):
        self.status.configure(text=text, fg=color)

    # ---------------------------------------------------------- screens
    # ------------------------------------------------------ the reveal
    def _cover(self, label: str):
        """Dark curtain over the content area while a screen is built."""
        c = tk.Canvas(self.content, bg=DARK_BG, highlightthickness=0, bd=0)
        c.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.tkraise(c)        # (Canvas.lift raises items, not the widget)

        def draw(_=None):
            c.delete("all")
            w, h = c.winfo_width(), c.winfo_height()
            c.create_text(w / 2, px(110), text="Preparing %s…" % label,
                          fill=DIM, font=F_SM)
            c.create_line(w / 2 - px(60), px(128), w / 2 + px(60), px(128),
                          fill=EDGE, width=1)
        c.bind("<Configure>", draw)
        return c

    def _charts_pending(self, root_w) -> bool:
        from ..theme.charts import Chart
        stack = [root_w]
        while stack:
            w = stack.pop()
            if isinstance(w, Chart) and getattr(w, "_job", None) and \
                    w.winfo_ismapped():
                return True
            stack.extend(w.winfo_children())
        return False

    def _reveal_when_drawn(self, screen, cover, tries=0):
        """Wait (event loop running) until layout settled and every chart
        has rendered, then wipe the curtain away."""
        if not cover.winfo_exists():
            return
        if tries < 40 and (tries < 2 or self._charts_pending(screen)):
            self.root.after(16, lambda: self._reveal_when_drawn(
                screen, cover, tries + 1))
            return
        H = max(cover.winfo_height(), 1)
        # Drop the curtain in one go and paint the whole screen before
        # anything moves. (Sliding the curtain made Tk repaint every newly
        # exposed strip while animations ran: black boxes, half labels.)
        if hasattr(screen, "prepare_reveal"):
            screen.prepare_reveal()      # e.g. radar drawn at its centre
        cover.destroy()
        self.root.update_idletasks()
        self._sweep(H, lambda: hasattr(screen, "on_reveal")
                    and screen.on_reveal())

    def _sweep(self, H, then=None, frames=13):
        """A thin gold line sweeps down over the fresh screen (moving a
        2-px strip is nearly free), then the entrance animations start."""
        line = tk.Frame(self.content, bg=EDGE_HI, height=max(1, px(1.5)))
        line.place(x=0, y=0, relwidth=1)
        tk.Misc.tkraise(line)

        done = [False]

        def finish():
            # exactly once — also when the sweep is cut short (a tab switch
            # or a new result destroys the line): otherwise the radar stayed
            # parked at its centre, every axis showing 20
            if not done[0]:
                done[0] = True
                if then:
                    self.root.after_idle(then)

        def step(k=0):
            if not line.winfo_exists():
                return
            t = anim.ease_out(k / frames)
            line.place_configure(y=int((H - px(2)) * t))
            if k < frames:
                line.after(anim.FRAME_MS, lambda: step(k + 1))
            else:
                line.destroy()
        line.bind("<Destroy>", lambda e: e.widget is line and finish(),
                  add="+")
        line.after(anim.FRAME_MS, step)

    def _mount(self, w):
        """Show ``w`` in the content cell (on top of everything else)."""
        if not w.winfo_manager():
            w.grid(row=0, column=0, sticky="nsew")
        w.tkraise()

    def _show_busy(self, text: str):
        """Visible "working on it" panel until the first result arrives
        (replaced by navigate() in _on_result)."""
        from .screens.base import panel
        self._clear_content()
        box = tk.Frame(self.content, bg=DARK_BG)
        self._mount(box)
        wrap = tk.Frame(box, bg=DARK_BG)
        wrap.pack(pady=(px(60), 0))
        inner, _ = panel(wrap, "ANALYSING", EDGE_HI, expand=False, fit=True,
                         min_width=420)
        ref = (" vs " + _level_label(self.reference)) if self.reference \
            else ""
        tk.Label(inner, text=text + ref, bg=PANEL, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(inner, text="Cached games show first; new games are "
                 "fetched in the background.", bg=PANEL, fg=DIM,
                 font=F_XS).pack(anchor="w", pady=(SP_XS, SP_M))
        bar = W.ProgressBar(inner, height=6)
        bar.pack(fill="x")
        bar.start()

    def _clear_content(self):
        for w in self.content.winfo_children():
            if w not in self.screens.values():
                safe_destroy(w, self._log)

    def _screen(self, name):
        if name not in self.screens:
            from . import screens
            try:
                cls = screens.REGISTRY[name]
            except ImportError:          # screen not built yet (M3 WIP)
                from .screens.base import Screen, empty_note, panel

                class cls(Screen):       # noqa: N801
                    def build(self):
                        inner, _ = panel(self.body, name.upper(), EDGE_HI,
                                         expand=False)
                        empty_note(inner, "This view is coming in the next "
                                   "part of milestone 3.")
            self.screens[name] = cls(self.content, self)
            self.dirty.add(name)
        return self.screens[name]

    def navigate(self, name, **kw):
        self.view = name
        if self.prefs.get("view") != name:
            self.prefs.set("view", name)
        self.nav.select(name, fire=False)
        if self.result is None or not self.player:
            return
        self._clear_content()
        if self.result.n_games == 0:
            self._empty_state()
            return
        new = name not in self.screens
        s = self._screen(name)
        # a screen built from scratch (new player / first visit) is built
        # behind a curtain and revealed only once it is fully drawn — no
        # half-painted charts or panels popping into place
        cover = None
        if name in self.dirty and (new or self._reveal_next):
            cover = self._cover(VIEW_LABEL.get(name, name))
        self._mount(s)
        if cover is not None:
            tk.Misc.tkraise(cover)
        if name in self.dirty:
            s.update_result(self.result)
            self.dirty.discard(name)
        if kw and hasattr(s, "focus"):
            s.focus(**kw)
        if cover is not None:
            self._reveal_next = False
            self._reveal_when_drawn(s, cover)

    def _empty_state(self):
        from .screens.base import panel
        box = tk.Frame(self.content, bg=DARK_BG)
        self._mount(box)
        inner, _ = panel(box, "NO GAMES", EDGE_HI, expand=False)
        tk.Label(inner, text="No games match these filters. Try another "
                 "role, champion or range.", bg=PANEL, fg=FG,
                 font=F_SM).pack(anchor="w")
        W.button(inner, "Reset filters", self.reset_filters).pack(
            anchor="w", pady=(SP_M, 0))

    def show_search(self):
        from .screens.search import SearchScreen
        self.bars.pack_forget()
        self._clear_content()
        if self.job:
            self.job.cancel()
        s = SearchScreen(self.content, self)
        self._mount(s)
        self.banner.set_context("ranked account analyzer")
        self.set_status("")

    def _show_bars(self):
        if not self.bars.winfo_ismapped():
            self.bars.pack(fill="x", before=self.content)

    # ---------------------------------------------------------- filters
    def _set_role(self, role):
        self.role = role
        self.reanalyze()

    def _set_queue(self, key):
        self.queue_key = key
        self.prefs.set("queue", key)
        if self.player:
            self.fetch(self.player["riot_id"], self.player["region"],
                       quiet=True)

    def _set_range(self, n):
        self.last_n = n
        self.reanalyze()

    def _set_champion(self, champ):
        if champ == "__more__":
            self.champ_row.select(self.champion, fire=False)
            self._champion_menu()
            return
        self.champion = champ
        self.reanalyze()

    def set_champion(self, champ):
        """Programmatic champion filter (e.g. clicking a table row)."""
        self._refresh_champ_options(force=champ)
        self.champ_row.select(champ, fire=False)
        self.champion = champ
        self.reanalyze()

    def reset_filters(self):
        self.role = self.champion = self.last_n = None
        self.role_row.select(None, fire=False)
        self.range_row.select(None, fire=False)
        self.champ_row.select(None, fire=False)
        self.reanalyze()

    def _champion_menu(self):
        from .screens.base import cname
        res = self._champ_source
        if not res:
            return
        m = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=FG,
                    activebackground=EDGE, activeforeground=FG, bd=0,
                    font=F_SM)
        for c in res:
            m.add_command(label="%s  (%d)" % (cname(c.name), c.games),
                          command=lambda n=c.name: self.set_champion(n))
        x = self.champ_row.winfo_rootx() + self.champ_row.winfo_width()
        m.tk_popup(x, self.champ_row.winfo_rooty() + px(24))

    _champ_source = None

    def _refresh_champ_options(self, force=None):
        from .screens.base import cname
        rows = self._champ_source or []
        top = [c.name for c in rows[:5]]
        if force and force not in top:
            top.append(force)
        opts = [(None, "All")] + [(c, cname(c)) for c in top]
        if len(rows) > 5:
            opts.append(("__more__", "More ▾"))
        self.champ_row.set_options(opts, self.champion)

    # ---------------------------------------------------------- fetching
    def fetch(self, riot_id: str, region: str, queue_key: str | None = None,
              count: int | None = None, quiet: bool = False):
        """Start (or restart) the background fetch for a player. quiet=True
        keeps the current screen (filter switch) instead of the loader."""
        from ..riot_client import clean_riot_id
        riot_id = clean_riot_id(riot_id)
        if queue_key:
            self.queue_key = queue_key
            self.queue_row.select(queue_key, fire=False)
        if count:
            self.count = count
        if self.job:
            self.job.cancel()
        try:
            reg = get_region(region)
            parse_riot_id(riot_id)
            client = default_client()
        except (ValueError, ReviewError) as e:
            self.show_error(e, retry=lambda: self.fetch(riot_id, region))
            return
        self._next_partial = PARTIAL_MIN
        if not quiet:
            self._reveal_next = True      # new player: reveal the result
        name, tag = parse_riot_id(riot_id)
        cached = self.cache.find_player(name, tag, reg.code)
        if not quiet:
            self.player = None
            self.result = None
            if cached:     # show what we already have while we update
                self.player = {"puuid": cached, "riot_id": riot_id,
                               "region": reg.code}
                self._show_bars()
                self._show_busy("Analysing %s…" % riot_id)
                self.reanalyze()
            else:
                self._show_loader(riot_id, reg.code)
        self.job = job = SyncJob(client, self.cache, riot_id, reg,
                                 queues=QUEUES[self.queue_key],
                                 count=self.count,
                                 on_progress=lambda p: self.post(
                                     self._progress, job, p))
        job.start()
        self._remember(riot_id, reg.code)
        self.root.after(200, lambda: self._poll(job))

    def _show_loader(self, riot_id, region):
        from .screens.loading import LoadingScreen
        self.bars.pack_forget()
        self._clear_content()
        self.loader = LoadingScreen(self.content, self, riot_id, region)
        self._mount(self.loader)
        self.banner.set_context(riot_id + " · " + region)

    def _progress(self, job, p):
        if job is not self.job:
            return
        loader = getattr(self, "loader", None)
        if loader is not None and loader.winfo_exists():
            loader.progress(p)
        if p.phase == "games" and p.total:
            eta = " · ~%d:%02d left" % divmod(int(p.eta_s or 0), 60) \
                if p.eta_s and p.done < p.total else ""
            self.set_status("Fetching %d/%d games…%s" % (p.done, p.total, eta))
            have = len(job.ids) - p.total + p.done
            if job.puuid and have >= self._next_partial and \
                    not self._analysing:
                self._next_partial = have * 2
                self._partial(job)
        elif p.phase in ("account", "rank", "ids"):
            self.set_status(p.message)

    def _show_rate_wait(self, job):
        """During Riot's rate-limit pauses nothing arrives for up to two
        minutes: say so, with a countdown, instead of looking frozen."""
        try:
            secs = job.client.limiter.waiting_for()
            since = getattr(job.client, "trouble_since", None)
        except Exception:
            return
        if since is not None and time.monotonic() - since > 3:
            msg = ("Can't reach Riot right now — retrying · %d s (check your "
                   "internet connection)" % (time.monotonic() - since))
        elif secs < 2:
            return
        else:
            m, sec = divmod(int(secs + 0.5), 60)
            msg = ("Waiting for Riot's rate limit · %d:%02d (personal keys "
                   "allow 100 requests per 2 min)" % (m, sec))
        loader = getattr(self, "loader", None)
        if loader is not None and loader.winfo_exists():
            loader.rate_wait(msg)
        else:
            base = self.status.cget("text").split(" · Waiting")[0]                 .split(" · Can't reach")[0]
            self.set_status(base + " · " + msg.split(" (")[0])

    def _partial(self, job):
        first = self.player is None
        self.player = {"puuid": job.puuid, "riot_id": job.riot_id,
                       "region": job.region.code}
        if first:
            self._show_bars()
        self.reanalyze(partial=True)

    def _poll(self, job):
        if job is not self.job:
            return
        if job._thread and job._thread.is_alive():
            self._show_rate_wait(job)
            self.root.after(200, lambda: self._poll(job))
            return
        if job.error is not None:
            if self.result is not None and isinstance(
                    job.error, (RateLimited, ServiceUnavailable)):
                self.set_status("Showing cached games — %s" % job.error, BAD)
                return
            self.show_error(job.error, retry=lambda: self.fetch(
                job.riot_id or "%s#%s" % (job.game_name, job.tag),
                job.region.code))
            return
        res = job.result
        if res.cancelled:
            return
        self._synced_at = time.time()
        self.player = {"puuid": res.puuid, "riot_id": res.riot_id,
                       "region": res.region}
        self._show_bars()
        self.reanalyze()

    # ---------------------------------------------------------- analysis
    def current_reference(self):
        """(region, level) of the active benchmark, or None."""
        from ..benchmark import MIN_USABLE
        if not self.reference or not self.player:
            return None
        n, _ = self.cache.benchmark_info(self.player["region"], self.reference)
        return (self.player["region"], self.reference) \
            if n >= MIN_USABLE else None

    def _set_reference(self, level):
        from ..benchmark import LEVELS, MIN_USABLE
        if level is None:
            self.reference = None
            self.prefs.set("reference", None)
            self.reanalyze()
            return
        region = self.player["region"] if self.player else \
            self.prefs.get("region", "EUW")
        n, _ = self.cache.benchmark_info(region, level)
        if n >= MIN_USABLE:
            self.reference = level
            self.prefs.set("reference", level)
            self.reanalyze()
            return
        self.vs_row.select(self.reference, fire=False)   # not ready yet
        job = self.bench_job
        if job and job._thread and job._thread.is_alive():
            self.bench_status.configure(
                text="%s benchmark is still being built…"
                % LEVELS[job.level].label, fg=EDGE_HI)
            return
        from .screens.benchdialog import BenchmarkDialog
        BenchmarkDialog(self, level)

    def build_benchmark(self, level: str, region: str, games: int):
        """Start a background benchmark build (one at a time)."""
        from ..benchmark import LEVELS, MIN_USABLE, BenchmarkJob
        if self.bench_job:
            self.bench_job.cancel()
        try:
            client = default_client()
            reg = get_region(region)
        except (ValueError, ReviewError) as e:
            self.bench_status.configure(text=str(e), fg=BAD)
            return
        label = LEVELS[level].label

        def prog(p):
            if p.phase == "ids":
                t = "%s benchmark: %s" % (label, p.message)
            elif p.phase == "games" and p.total:
                m = int((p.eta_s or 0) // 60)
                t = "%s benchmark · %d/%d games%s" % (
                    label, p.done, p.total,
                    " · ~%d min left" % max(m, 1) if p.done < p.total
                    else "")
            else:
                return
            self.post(lambda: self.bench_status.configure(text=t,
                                                          fg=EDGE_HI))
        job = BenchmarkJob(client, self.cache, reg, level, games=games,
                           on_progress=prog)
        self.bench_job = job
        job.start()

        def poll():
            if job is not self.bench_job:
                return
            if job._thread and job._thread.is_alive():
                self.root.after(500, poll)
                return
            n, _ = self.cache.benchmark_info(region, level)
            if job.error is not None and n < MIN_USABLE:
                self.bench_status.configure(text="%s benchmark failed: %s"
                                            % (label, job.error), fg=BAD)
                return
            if n < MIN_USABLE:
                self.bench_status.configure(text="%s benchmark: only %d "
                                            "games so far" % (label, n),
                                            fg=DIM)
                return
            self.bench_status.configure(text="%s benchmark ready · %d games"
                                        % (label, n), fg=GOOD)
            self.reference = level
            self.prefs.set("reference", level)
            self.vs_row.select(level, fire=False)
            self.reanalyze()
        self.root.after(500, poll)

    def current_filter(self) -> Filter:
        return Filter(queues=QUEUES[self.queue_key], role=self.role,
                      champion=self.champion, last_n=self.last_n)

    def reanalyze(self, partial: bool = False):
        if not self.player:
            return
        self.gen += 1
        gen, flt = self.gen, self.current_filter()
        puuid = self.player["puuid"]
        if not partial:
            self.set_status("Analyzing%s…" % (
                " vs %s" % _level_label(self.reference)
                if self.reference else ""))
        for s in self.screens.values():
            if hasattr(s, "set_busy"):
                s.set_busy(True)
        self._analysing += 1

        def finished():
            self._analysing = max(0, self._analysing - 1)

        def work():
            try:
                ref = self.current_reference()
                res = analyze_player(self.cache, puuid, flt, self.legendary,
                                     limit=self.count, reference=ref)
                base = None
                if flt.champion:   # champion options come from all champs
                    base = analyze_player(self.cache, puuid, Filter(
                        queues=flt.queues, role=flt.role,
                        last_n=flt.last_n), self.legendary, limit=self.count,
                        reference=ref)
                self.post(self._on_result, gen, res, base, partial)
            except Exception as e:      # never a raw traceback in the UI
                self._log(e)
                self.post(self.show_error, e, None)
            finally:
                self.post(finished)
        threading.Thread(target=work, daemon=True).start()

    def _on_result(self, gen, res, base, partial):
        if gen != self.gen:
            return
        prev = self.result
        # the same games, filter and scale as what's on screen (e.g. the
        # background fetch finished without new games): keep the screen —
        # rebuilding it made panels blink and the radar jump for nothing
        same = (prev is not None and prev.puuid == res.puuid
                and prev.chron_ids == res.chron_ids
                and prev.filter == res.filter
                and prev.reference_label == res.reference_label
                and self.view in self.screens and self.view not in self.dirty
                and bool(self.content.winfo_children()))
        self.result = res
        if same:
            self._finish_status(res, partial)
            return
        self._champ_source = (base or res).champions
        self._refresh_champ_options(self.champion)
        hist = self.cache.rank_history(self.player["puuid"],
                                       "RANKED_FLEX_SR" if self.queue_key
                                       == "flex" else "RANKED_SOLO_5x5")
        self.player["rank"] = rank_text(hist[-1] if hist else None)
        self.banner.set_context("%s · %s · %s" % (
            self.player["riot_id"], self.player["region"],
            self.player["rank"]))
        self.dirty = set(self.screens)
        for s in self.screens.values():
            if hasattr(s, "set_busy"):
                s.set_busy(False)
        self._show_bars()
        self.navigate(self.view)
        self._finish_status(res, partial)

    def _finish_status(self, res, partial):
        job_running = self.job and self.job._thread and \
            self.job._thread.is_alive()
        if not job_running:
            self.set_status("Done · %d games · %s · %s%s%s" % (
                res.n_games, " + ".join(QUEUE_LABEL[q] for q in
                                        QUEUES[self.queue_key]),
                self.player["region"],
                " · vs %s" % res.reference_label if res.reference_label
                else "", self._updated_text()))
        elif partial:
            self.set_status(self.status.cget("text") + " · partial results")

    def _updated_text(self) -> str:
        if not self._synced_at:
            return ""
        m = int((time.time() - self._synced_at) // 60)
        return " · updated %s" % ("just now" if m < 1 else
                                   "%d min ago" % m if m < 60 else
                                   "%d h ago" % (m // 60))

    def _tick_updated(self):
        """Keep "updated N min ago" current while the app sits idle."""
        try:
            txt = self.status.cget("text")
            if txt.startswith("Done ·") and " · updated " in txt:
                self.set_status(txt.split(" · updated ")[0] +
                                self._updated_text())
            self.root.after(30000, self._tick_updated)
        except tk.TclError:
            pass

    # -------------------------------------------------------- shortcuts
    def refresh(self):
        """Fetch only new games for the current player (F5)."""
        if self.player and not (self.job and self.job._thread
                                and self.job._thread.is_alive()):
            self.fetch(self.player["riot_id"], self.player["region"],
                       quiet=True)

    def copy_view(self):
        """The window as a picture on the clipboard (Ctrl+Shift+C)."""
        from .snapshot import capture, copy_image
        try:
            copy_image(capture(self.root))
            self.set_status("Copied — paste it anywhere (Ctrl+V).", GOOD)
        except Exception as e:                 # noqa: BLE001
            self._log(e)
            self.set_status("Couldn't copy the picture.", BAD)

    def _install_shortcuts(self):
        r = self.root
        keys = [k for k, _ in VIEWS]

        def has_result():
            return self.result is not None and self.player is not None                 and self.bars.winfo_ismapped()

        def jump(i):
            if has_result() and i < len(keys):
                self.navigate(keys[i])
            return "break"

        def step(d):
            if has_result():
                i = keys.index(self.view) if self.view in keys else 0
                self.navigate(keys[(i + d) % len(keys)])
            return "break"
        def ctrl(e):
            # one handler for every Ctrl+key: Ctrl+1…0 tabs, Ctrl+(Shift+)Tab
            # next/previous tab, Ctrl+R refresh, Ctrl+Shift+C copy view
            k, shift = e.keysym, bool(e.state & 0x1)
            if k.isdigit() and len(k) == 1:
                return jump((int(k) - 1) % 10)
            if k in ("Tab", "ISO_Left_Tab"):
                return step(-1 if shift or k == "ISO_Left_Tab" else 1)
            if k in ("Next", "Prior"):            # Ctrl+PageDown / PageUp
                return step(1 if k == "Next" else -1)
            if k.lower() == "r":
                self.refresh()
                return "break"
            if k.lower() == "c" and shift:
                self.copy_view()
                return "break"
            return None
        r.bind_all("<Control-KeyPress>", ctrl)
        # Tk's own focus traversal binds Ctrl+Tab more specifically on the
        # same "all" tag, which would win over the generic handler
        r.bind_all("<Control-Key-Tab>", ctrl)
        r.bind_all("<Control-Shift-Key-Tab>", ctrl)
        r.bind_all("<F5>", lambda e: (self.refresh(), "break")[1])

        def esc(e):
            # back out of an opened game (Matches) — dialogs bind their own
            if e.widget.winfo_toplevel() is not r:
                return
            s = self.screens.get(self.view)
            if s is not None and getattr(s, "game_id", None) and                     hasattr(s, "_back"):
                s._back()
        r.bind("<Escape>", esc)

    # ------------------------------------------------------------ errors
    def show_error(self, e, retry=None):
        if isinstance(e, PlayerNotFound):
            title, msg = "PLAYER NOT FOUND", str(e) + " Check the spelling, " \
                "the #TAG and the region."
        elif isinstance(e, InvalidKey) and config.key_source() == "built_in":
            # friends on a release build never chose a key: the dev-key
            # advice ("expires every 24 h…") would only confuse them
            title, msg = "API KEY", (
                "The Riot API key built into this copy of Review has "
                "stopped working. Ask whoever sent you Review for an updated "
                "version — or paste your own free key from "
                "developer.riotgames.com in ⚙ Settings.")
        elif isinstance(e, (InvalidKey, MissingKey)):
            title, msg = "API KEY", str(e)
        elif isinstance(e, RateLimited):
            title, msg = "RATE LIMITED", str(e)
        elif isinstance(e, ServiceUnavailable):
            title, msg = "RIOT API UNAVAILABLE", str(e)
        elif isinstance(e, (ReviewError, ValueError)):
            title, msg = "CAN'T DO THAT", str(e)
        else:
            self._log(e)
            title, msg = "SOMETHING WENT WRONG", (
                "REVIEW hit an unexpected error (%s). Details were written to "
                "%s." % (type(e).__name__, config.data_dir() / "review.log"))
        from .screens.base import panel
        self.bars.pack_forget()
        self._clear_content()
        box = tk.Frame(self.content, bg=DARK_BG, pady=SP_L)
        self._mount(box)
        inner, _ = panel(box, title, BAD, expand=False)
        tk.Label(inner, text=msg, bg=PANEL, fg=FG, font=F_SM, justify="left",
                 wraplength=px(700)).pack(anchor="w")
        btns = tk.Frame(inner, bg=PANEL)
        btns.pack(anchor="w", pady=(SP_L, 0))
        if retry:
            W.button(btns, "Retry", retry).pack(side="left")
        if isinstance(e, (InvalidKey, MissingKey)):
            W.button(btns, "Open settings", self.show_settings).pack(
                side="left", padx=(SP_S if retry else 0, 0))
        W.button(btns, "New search", self.show_search, fg=DIM).pack(
            side="left", padx=(SP_S, 0))
        self.set_status("")

    def _start_watchdog(self):
        """If the UI thread ever stops answering for > 3 s, write every
        thread's stack to review.log (once per stall) — evidence for
        freezes that can't be reproduced on another machine."""
        import sys as _sys
        self._beat = time.monotonic()

        def beat():
            self._beat = time.monotonic()
            try:
                self.root.after(250, beat)
            except Exception:
                pass

        try:
            limit = float(config.setting("REVIEW_WATCHDOG_S", "3"))
        except ValueError:
            limit = 3.0

        def watch():
            reported = False
            while True:
                time.sleep(0.25)
                lag = time.monotonic() - self._beat
                if lag > limit and not reported:
                    reported = True
                    names = {t.ident: t.name for t in threading.enumerate()}
                    lines = ["UI THREAD STALLED %.1f s" % lag]
                    for tid, fr in _sys._current_frames().items():
                        lines.append("--- %s" % names.get(tid, tid))
                        lines.append("".join(
                            traceback.format_stack(fr)[-14:]))
                    try:
                        with open(config.data_dir() / "review.log", "a",
                                  encoding="utf-8") as f:
                            f.write("%s\n%s\n" % (time.ctime(),
                                                   "\n".join(lines)))
                    except OSError:
                        pass
                elif lag < 1:
                    reported = False
        self.root.after(250, beat)
        threading.Thread(target=watch, daemon=True,
                         name="ui-watchdog").start()

    def _log(self, e):
        try:
            with open(config.data_dir() / "review.log", "a",
                      encoding="utf-8") as f:
                f.write("%s\n%s\n" % (time.ctime(), "".join(
                    traceback.format_exception(e))))
        except OSError:
            pass

    # ------------------------------------------------------------- misc
    def _clean_saved_ids(self):
        """Recent searches / Compare lists saved before Riot IDs were
        cleaned may hold pasted line breaks ('Ryuk\n#adc')."""
        from ..riot_client import clean_riot_id
        rec, seen = [], set()
        for r in self.prefs.get("recent", []) or []:
            rid = clean_riot_id(r.get("riot_id", ""))
            k = (rid.lower(), r.get("region"))
            if rid and k not in seen:
                seen.add(k)
                rec.append(dict(r, riot_id=rid))
        if rec != (self.prefs.get("recent", []) or []):
            self.prefs.set("recent", rec)
        cmp_ = self.prefs.get("compare", []) or []
        clean = [clean_riot_id(r) for r in cmp_]
        if clean != cmp_:
            self.prefs.set("compare", clean)

    def _remember(self, riot_id, region):
        from ..riot_client import clean_riot_id
        riot_id = clean_riot_id(riot_id)
        rec = [r for r in self.prefs.get("recent", [])
               if r.get("riot_id", "").lower() != riot_id.lower()
               or r.get("region") != region]
        rec.insert(0, {"riot_id": riot_id, "region": region})
        self.prefs.set("recent", rec[:8])
        self.prefs.set("region", region)

    def open_game(self, match_id: str):
        self.navigate("matches")
        self.screens["matches"].open_game(match_id)

    def show_settings(self):
        from .screens.settings import SettingsDialog
        SettingsDialog(self)

    def close(self):
        """The X button. Nothing here may stop the window from closing:
        every step is guarded and destroy() always runs. The process then
        exits without waiting for in-flight downloads (see __main__).
        A daemon thread ends the process after 3 s no matter what, so the
        X button can never leave a living window/process behind."""
        import os as _os
        self._trace("close requested")
        if getattr(self, "exit_on_close", True):
            def hard_exit():
                time.sleep(3)
                self._trace("close: forced exit (UI did not finish in 3 s)")
                _os._exit(0)
            threading.Thread(target=hard_exit, daemon=True,
                             name="exit-guard").start()

        def save_geo():
            g = self.root.geometry()
            if _parse_geo(g) and self._on_screen(*_parse_geo(g)):
                self.prefs.set("geometry", g)   # never remember off-screen
        for step in (save_geo,
                     lambda: self.job and self.job.cancel(),
                     lambda: self.bench_job and self.bench_job.cancel(),
                     lambda: self.tooltip.hide()):
            try:
                step()
            except Exception as e:
                self._log(e)
        safe_destroy(self.root, self._log)

    def _trace(self, msg: str) -> None:
        try:
            with open(config.data_dir() / "review.log", "a",
                      encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.ctime(), msg))
        except OSError:
            pass

    def run(self):
        self.root.mainloop()
