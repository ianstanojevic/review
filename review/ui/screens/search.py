"""Search: Riot ID, region, queue, game count, recent searches."""
from __future__ import annotations

import tkinter as tk

from ...riot_client import PRIMARY_REGIONS, REGIONS, parse_riot_id
from ...theme import anim
from ...theme import widgets as W
from ...theme.tokens import (BAD, DARK_BG, DIM, EDGE_HI, F_MD, F_SM, F_XS,
                             F_XS_B, FG, HOVER, PANEL, PANEL2, SP_L, SP_M,
                             SP_S, SP_XS)
from ...theme.widgets import px
from .base import panel

COUNTS = [(50, "50"), (100, "100"), (150, "150"), (300, "300"), (500, "500")]


class SearchScreen(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=DARK_BG)
        self.app = app
        wrap = tk.Frame(self, bg=DARK_BG)
        wrap.pack(pady=(px(40), 0))
        inner, _ = panel(wrap, "ANALYZE A PLAYER", EDGE_HI, expand=False,
                         fit=True)
        inner.configure(padx=px(6))

        W.caption(inner, "RIOT ID", pady=(SP_XS, SP_XS))
        self.entry = W.entry(inner, width=34, font=("Segoe UI", 13))
        self.entry.pack(fill="x", ipady=px(5))
        self.entry.bind("<Return>", lambda e: self.go())
        tk.Label(inner, text="Name#TAG — e.g. flintgnuggaren#ian", bg=PANEL,
                 fg=DIM, font=F_XS).pack(anchor="w", pady=(SP_XS, 0))

        rows = tk.Frame(inner, bg=PANEL)
        rows.pack(fill="x", pady=(SP_L, 0))
        region = app.prefs.get("region", "EUW")
        opts = [(r, r) for r in PRIMARY_REGIONS]
        if region not in PRIMARY_REGIONS:
            opts.append((region, region))
        opts.append(("__more__", "…"))
        self.region = W.TabRow(rows, "REGION", opts, region, bg=PANEL,
                               on_change=self._region)
        self.region.pack(anchor="w", pady=(0, SP_S))
        self.queue = W.TabRow(rows, "QUEUE", [("solo", "Solo/Duo"),
                                              ("flex", "Flex"),
                                              ("both", "Both")],
                              app.queue_key, style="teal", bg=PANEL)
        self.queue.pack(anchor="w", pady=(0, SP_S))
        cnt = app.count if app.count in dict(COUNTS) else 150
        self.games = W.TabRow(rows, "GAMES", COUNTS, cnt, style="teal",
                              bg=PANEL)
        self.games.pack(anchor="w")

        act = tk.Frame(inner, bg=PANEL)
        act.pack(fill="x", pady=(SP_L, 0))
        W.button(act, "Analyze", self.go, gold=True).pack(side="left")
        self.err = tk.Label(act, text="", bg=PANEL, fg=BAD, font=F_SM)
        self.err.pack(side="left", padx=(SP_L, 0))
        tk.Label(inner, text="First fetch takes ~2.5 s per game on a "
                 "personal API key (150 games ≈ 6 min); later runs only "
                 "fetch new games.", bg=PANEL, fg=DIM, font=F_XS,
                 wraplength=px(440), justify="left").pack(anchor="w",
                                                          pady=(SP_M, 0))

        recent = app.prefs.get("recent", [])
        if recent:
            W.caption(inner, "RECENT", pady=(SP_L, SP_XS))
            for r in recent[:6]:
                self._recent_row(inner, r)
        self.entry.focus_set()

    def _recent_row(self, parent, r):
        row = tk.Label(parent, text="%s   ·   %s" % (r["riot_id"],
                                                     r["region"]),
                       bg=PANEL2, fg=FG, font=F_SM, anchor="w", padx=px(8),
                       pady=px(4), cursor="hand2")
        row.pack(fill="x", pady=1)
        anim.hoverfx(row, HOVER)
        row.bind("<Button-1>", lambda e: self._pick(r))

    def _pick(self, r):
        self.entry.delete(0, "end")
        self.entry.insert(0, r["riot_id"])
        if r["region"] not in self.region.btns:
            self._add_region(r["region"])
        self.region.select(r["region"], fire=False)
        self.go()

    def _region(self, key):
        if key != "__more__":
            return
        m = tk.Menu(self, tearoff=0, bg=PANEL2, fg=FG, activebackground=HOVER,
                    activeforeground=FG, font=F_SM)
        for code in REGIONS:
            if code not in PRIMARY_REGIONS:
                m.add_command(label=code, command=lambda c=code:
                              self._add_region(c))
        m.tk_popup(self.region.winfo_rootx() + self.region.winfo_width(),
                   self.region.winfo_rooty())

    def _add_region(self, code):
        opts = [(k, v) for k, v in self.region.texts.items() if k != "__more__"]
        if code not in dict(opts):
            opts.append((code, code))
        opts.append(("__more__", "…"))
        self.region.set_options(opts, code)

    def go(self):
        text = self.entry.get().strip()
        try:
            parse_riot_id(text)
        except ValueError as e:
            self.err.configure(text=str(e))
            return
        region = self.region.value
        if region == "__more__":
            region = "EUW"
        self.app.prefs.set("count", self.games.value)
        self.app.fetch(text, region, self.queue.value, self.games.value)
