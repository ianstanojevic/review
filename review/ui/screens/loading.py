"""Loading: progress bar + status line while the first fetch runs."""
from __future__ import annotations

import tkinter as tk

from ...theme import widgets as W
from ...theme.tokens import (DARK_BG, DIM, EDGE_HI, F_SM, F_XS, FG, PANEL,
                             SP_L, SP_M, SP_S)
from ...theme.widgets import px
from .base import panel


class LoadingScreen(tk.Frame):
    def __init__(self, master, app, riot_id, region):
        super().__init__(master, bg=DARK_BG)
        self.app = app
        wrap = tk.Frame(self, bg=DARK_BG)
        wrap.pack(pady=(px(60), 0))
        inner, self.nlbl = panel(wrap, "FETCHING", EDGE_HI, right="",
                                 expand=False, fit=True)
        tk.Label(inner, text="%s · %s" % (riot_id, region), bg=PANEL, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.msg = tk.Label(inner, text="Looking up the account…", bg=PANEL,
                            fg=DIM, font=F_SM, width=56, anchor="w")
        self.msg.pack(anchor="w", pady=(SP_S, SP_M))
        self.bar = W.ProgressBar(inner, height=6)
        self.bar.pack(fill="x")
        self.bar.start()
        self.eta = tk.Label(inner, text="", bg=PANEL, fg=DIM, font=F_XS)
        self.eta.pack(anchor="w", pady=(SP_S, 0))
        tk.Label(inner, text="Results appear as soon as %d games are in; "
                 "everything fetched is cached, so cancelling loses nothing."
                 % 20, bg=PANEL, fg=DIM, font=F_XS, wraplength=px(420),
                 justify="left").pack(anchor="w", pady=(SP_M, 0))
        W.button(inner, "Cancel", self.cancel, fg=DIM).pack(
            anchor="w", pady=(SP_L, 0))

    def rate_wait(self, msg):
        self.eta.configure(text=msg, fg=EDGE_HI)

    def progress(self, p):
        self.msg.configure(text=p.message)
        self.eta.configure(fg=DIM)
        if p.phase == "games" and p.total:
            self.bar.set(p.done / p.total)
            self.nlbl.configure(text="%d / %d" % (p.done, p.total))
            if p.eta_s and p.done < p.total:
                m, s = divmod(int(p.eta_s), 60)
                self.eta.configure(text="about %d:%02d left (Riot's rate "
                                   "limit: 100 requests / 2 min)" % (m, s))
            else:
                self.eta.configure(text="")
        elif p.phase == "done":
            self.bar.set(1.0)
            self.bar.stop()

    def cancel(self):
        if self.app.job:
            self.app.job.cancel()
        self.bar.stop()
        self.app.show_search()
