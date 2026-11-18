"""Settings (⚙): default game count, cache housekeeping, key status."""
from __future__ import annotations

import os
import tkinter as tk

from ... import __version__, config
from ...theme import anim
from ...theme import widgets as W
from ...theme.tokens import (DARK_BG, DIM, EDGE, EDGE_HI, F_SM, F_XS, FG,
                             GOOD, BAD, PANEL, SP_L, SP_M, SP_S)
from ...theme.widgets import px
from .search import COUNTS


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Settings")
        self.configure(bg=DARK_BG)
        self.resizable(False, False)
        self.transient(app.root)
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        W.enable_dark_titlebar(self)
        p = W.CutPanel(self, padx=14)
        p.pack(padx=px(12), pady=px(12))
        inner = p.inner
        W.section_title(inner, "SETTINGS", EDGE_HI)
        self.games = W.TabRow(inner, "DEFAULT GAMES", COUNTS, app.count,
                              style="teal", bg=PANEL,
                              on_change=self._count)
        self.games.pack(anchor="w")
        self._key_section(inner)
        self._benchmarks(inner)
        W.caption(inner, "CACHE")
        s = app.cache.stats()
        tk.Label(inner, text="%d matches · %d timelines · %d players\n%s" % (
            s["matches"], s["timelines"], s["players"], config.db_path()),
            bg=PANEL, fg=DIM, font=F_XS, justify="left").pack(anchor="w")
        row = tk.Frame(inner, bg=PANEL)
        row.pack(anchor="w", pady=(SP_M, 0))
        W.button(row, "Open data folder", lambda: os.startfile(
            config.data_dir())).pack(side="left")
        W.button(row, "Recompute analyses", self._clear).pack(
            side="left", padx=(SP_S, 0))
        tk.Label(inner, text="REVIEW %s · data: Riot API + Data Dragon" %
                 __version__, bg=PANEL, fg=DIM, font=F_XS).pack(
                     anchor="w", pady=(SP_L, 0))
        from ..app import LEGAL           # required by Riot's API policy
        tk.Label(inner, text=LEGAL, bg=PANEL, fg=DIM, font=("Segoe UI", 7),
                 justify="left", wraplength=px(380)).pack(
                     anchor="w", pady=(SP_S, 0))
        self.update_idletasks()
        x = app.root.winfo_rootx() + app.root.winfo_width() - \
            self.winfo_reqwidth() - px(30)
        y = app.root.winfo_rooty() + px(60)
        self.geometry("+%d+%d" % (x, y))
        anim.fade_in(self)

    def _key_section(self, inner):
        """Paste a new Riot key (development keys expire every 24 h)."""
        W.caption(inner, "RIOT API KEY")
        key = config.api_key()
        self.key_state = tk.Label(inner, text=("in use: …%s" % key[-4:])
                                  if key else "no key yet", bg=PANEL,
                                  fg=GOOD if key else BAD, font=F_SM)
        self.key_state.pack(anchor="w")
        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", pady=(SP_S, 0))
        self.key_entry = W.entry(row, width=36, font=F_SM)
        self.key_entry.pack(side="left", ipady=3)
        self.key_entry.bind("<Return>", lambda e: self._save_key())
        W.button(row, "Save & test", self._save_key, font=F_SM).pack(
            side="left", padx=(SP_S, 0))
        tk.Label(inner, text="Get one at developer.riotgames.com "
                 "(RGAPI-…).", bg=PANEL, fg=DIM, font=F_XS).pack(
                     anchor="w", pady=(SP_S, 0))

    def _save_key(self):
        import threading
        key = self.key_entry.get().strip()
        if not key:
            return
        if not key.startswith("RGAPI-"):
            self.key_state.configure(text="that doesn't look like a Riot key "
                                     "(RGAPI-…)", fg=BAD)
            return
        config.save_key(key)
        self.key_entry.delete(0, "end")
        self.key_state.configure(text="saved …%s · testing…" % key[-4:],
                                 fg=DIM)
        app = self.app

        def test():
            from ...riot_client import (InvalidKey, ReviewError,
                                        default_client, get_region)
            try:
                c = default_client()
                c.get(get_region(app.prefs.get("region", "EUW")).platform,
                      "/lol/status/v4/platform-data")
                msg, col = "in use: …%s · works" % key[-4:], GOOD
            except InvalidKey:
                msg, col = "…%s · Riot rejected this key" % key[-4:], BAD
            except ReviewError as e:
                msg, col = "…%s · saved, couldn't test (%s)" % (key[-4:],
                                                                 e), DIM
            app.post(lambda: self.key_state.winfo_exists()
                     and self.key_state.configure(text=msg, fg=col))
        threading.Thread(target=test, daemon=True).start()

    def _benchmarks(self, inner):
        import time as _t
        from ...benchmark import LEVELS
        region = self.app.player["region"] if self.app.player else \
            self.app.prefs.get("region", "EUW")
        W.caption(inner, "BENCHMARKS · %s" % region)
        for key, lv in LEVELS.items():
            n, t = self.app.cache.benchmark_info(region, key)
            row = tk.Frame(inner, bg=PANEL)
            row.pack(fill="x", pady=(0, SP_S))
            when = _t.strftime(" · updated %d %b", _t.localtime(t)) if t \
                else ""
            tk.Label(row, text="%-8s %s" % (lv.label, ("%d games%s" % (
                n, when)) if n else "not built"), bg=PANEL,
                fg=FG if n else DIM, font=F_SM, anchor="w").pack(side="left")
            W.button(row, "Fetch more" if n else "Build",
                     lambda k=key: self._open_bench(k), font=F_SM).pack(
                         side="right", padx=(SP_M, 0))

    def _open_bench(self, key):
        from .benchdialog import BenchmarkDialog
        self.destroy()
        BenchmarkDialog(self.app, key)

    def _count(self, n):
        self.app.count = n
        self.app.prefs.set("count", n)

    def _clear(self):
        with self.app.cache._lock, self.app.cache._db:
            self.app.cache._db.execute("DELETE FROM analysis_cache")
            self.app.cache._db.execute("DELETE FROM features")
        self.app.reanalyze()
        self.destroy()
