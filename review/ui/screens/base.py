"""Screen base class and layout helpers shared by all REVIEW screens."""
from __future__ import annotations

import math
import tkinter as tk

from ... import static
from ...analysis import fmt_value
from ...analysis.stats import Rate
from ...theme import widgets as W
from ...theme.charts import BinBar
from ...theme.tokens import (BAD, DARK_BG, DIM, EDGE_HI, F_SM, F_SM_B, F_XS,
                             F_XS_B, FG, GOOD, PANEL, PANEL2, SP_L, SP_M,
                             SP_S, SP_XS)
from ...theme.widgets import px


class Screen(tk.Frame):
    """A view under the tab rows. ``scroll=True`` wraps content in a
    ScrollArea; build UI in ``build()``, fill it in ``update(result)``."""
    title = ""
    scroll = True

    def __init__(self, master, app):
        super().__init__(master, bg=DARK_BG)
        self.app = app
        self.tip = app.tooltip
        if self.scroll:
            self.area = W.ScrollArea(self)
            self.area.pack(fill="both", expand=True)
            self.body = self.area.body
        else:
            self.body = self
        self.build()

    def build(self):
        pass

    def update_result(self, res):
        pass

    def _radars(self):
        from ...theme.radar import RadarChart
        stack, out = [self], []
        while stack:
            w = stack.pop()
            if isinstance(w, RadarChart) and not w.thumb and w.axes:
                out.append(w)
            stack.extend(w.winfo_children())
        return out

    def prepare_reveal(self):
        """Just before the curtain drops: put entrance animations at their
        start state, so nothing visibly jumps back before animating."""
        from ...theme.radar import SCALE_LO
        for r in self._radars():
            r.shown = [SCALE_LO] * len(r.axes)
            r.render()

    def on_reveal(self):
        """After the gold sweep: every big radar grows out of its centre."""
        for r in self._radars():
            r.grow()


# --------------------------------------------------------------- helpers

def panel(parent, title: str | None = None, color: str = EDGE_HI,
          right: str | None = None, side=None, expand=True, padx=(0, 0),
          pady=(0, 0), grow=False, fit=False, min_width=0):
    """CutPanel with a coloured section header. Returns (inner, right_lbl).
    ``fit=True`` sizes the panel to its content (for side panels that
    don't expand); otherwise it fills the width it is given."""
    p = W.CutPanel(parent, padx=14, fill=not (grow or fit), grow=grow)
    if side:
        p.pack(side=side, fill="both", expand=expand, padx=padx, pady=pady)
    else:
        p.pack(fill="both", expand=expand, padx=padx, pady=pady)
    if min_width:
        tk.Frame(p.inner, bg=PANEL, width=px(min_width), height=0).pack()
    rl = None
    if title:
        rl = W.section_title(p.inner, title, color,
                             right="" if right is None else right)
    return p.inner, rl


def row(parent, bg=DARK_BG, pady=(0, 0)):
    f = tk.Frame(parent, bg=bg)
    f.pack(fill="both", expand=True, pady=pady)
    return f


def chart_block(parent, chart_cls, title: str, height=200, bg=PANEL,
                tip=None, **kw):
    """Caption row (title left, n right) + chart + takeaway line."""
    head = tk.Frame(parent, bg=bg)
    head.pack(fill="x", pady=(SP_M, SP_XS))
    tk.Label(head, text=title, bg=bg, fg=DIM, font=F_XS_B).pack(side="left")
    nlbl = tk.Label(head, text="", bg=bg, fg=DIM, font=F_XS)
    nlbl.pack(side="right")
    ch = chart_cls(parent, tip, height=height, **kw)
    ch.pack(fill="x")
    # the one-line takeaway is kept for PNG exports only — on screen the
    # chart speaks for itself (user preference)
    take = tk.Label(parent, text="", bg=bg, fg=FG, font=F_SM, anchor="w",
                    justify="left", wraplength=px(560))
    take.bind("<Configure>", lambda e: take.configure(
        wraplength=max(e.width - 4, 100)))
    ch._title = title

    def meta(n_text="", takeaway=""):
        nlbl.configure(text=n_text)
        take.configure(text=takeaway)
        ch.set_meta(title, n_text, takeaway)
    ch.meta = meta
    return ch


def cname(champ: str) -> str:
    return static.champion_name(champ)


def wr_text(r: Rate) -> str:
    return "%.0f%%" % (100 * r.p) if r.n else "—"


def pm_text(r: Rate) -> str:
    if not r.n:
        return ""
    lo, hi = r.ci
    return "±%.0f" % (100 * (hi - lo) / 2)


def rate_bins(bins) -> list[BinBar]:
    out = []
    for b in bins:
        lo, hi = b.rate.ci if b.rate.n else (float("nan"), float("nan"))
        out.append(BinBar(b.label, b.rate.p if b.rate.n else float("nan"),
                          lo, hi, b.rate.n))
    return out


def mean_text(m, fmt="%+.0f") -> str:
    if not m.n or math.isnan(m.mean):
        return "—"
    if math.isnan(m.se):
        return fmt % m.mean
    return "%s ±%s" % (fmt % m.mean, fmt.replace("+", "") % m.half)


# metric family -> the radar axis whose line icon represents it
FAMILY_ICON = {"kda": "fighting", "damage": "fighting", "cs": "farming",
               "gold": "farming", "lane": "farming", "vision": "vision",
               "early": "aggression", "deaths": "survival",
               "tank": "survival", "kp": "teamplay", "teamplay": "teamplay",
               "objectives": "objectives"}
_ICON_CACHE: dict = {}


def axis_icon(key: str, size: int, color: str, bg: str = PANEL2):
    """A radar-axis line icon as a PhotoImage (cached)."""
    ck = (key, size, color, bg)
    if ck not in _ICON_CACHE:
        from PIL import ImageTk
        from ...theme.pen import Pen
        from ...theme.radar import draw_icon
        pen = Pen(size, size, bg=bg)
        draw_icon(pen, key, size / 2, size / 2, size * 0.78, color)
        _ICON_CACHE[ck] = ImageTk.PhotoImage(pen.finish())
    return _ICON_CACHE[ck]


def finding_card(parent, f, color: str, bg=PANEL, app=None,
                 group: str = "peers"):
    """One strength/weakness, compact: icon · title · the numbers · a
    confidence chip. The full sentence shows on hover; click the card to
    unfold the context and the concrete tip."""
    from ...analysis.metrics import METRICS
    box = tk.Frame(parent, bg=PANEL2, padx=px(8), pady=px(5),
                   cursor="hand2")
    box.pack(fill="x", pady=(0, px(4)))
    top = tk.Frame(box, bg=PANEL2)
    top.pack(fill="x")
    tk.Frame(top, bg=color, width=px(3)).pack(side="left", fill="y",
                                              padx=(0, px(8)))
    size = px(26)
    img = None
    champ = f.key.startswith("champion:")
    if champ and app is not None:
        img = app.icons.get(f.key.split(":", 1)[1], size, lambda: None)
    if img is None:
        fam = METRICS[f.key].family if f.key in METRICS else ""
        img = axis_icon(FAMILY_ICON.get(fam, "fighting") if not champ
                        else "fighting", size, color)
    ic = tk.Label(top, image=img, bg=PANEL2)
    ic.image = img
    ic.pack(side="left", padx=(0, px(8)))
    mid = tk.Frame(top, bg=PANEL2)
    mid.pack(side="left", fill="x", expand=True)
    title = cname(f.title) if champ else f.title
    tk.Label(mid, text=title, bg=PANEL2, fg=FG, font=F_SM_B,
             anchor="w").pack(fill="x")
    if champ:
        nums = "%.0f%% WR vs %.0f%% overall · n=%d" % (100 * f.value,
                                                       100 * f.peer, f.n)
    else:
        you = fmt_value(f.key, f.value)
        nums = "%s vs %s %s · n=%d" % (you, fmt_value(f.key, f.peer),
                                       group, f.n)
    tk.Label(mid, text=nums, bg=PANEL2, fg=color, font=F_XS,
             anchor="w").pack(fill="x")
    chip = tk.Label(top, text=f.confidence.upper(), bg=PANEL2,
                    fg=EDGE_HI if f.confidence == "high" else DIM,
                    font=F_XS_B)
    chip.pack(side="right", anchor="n")
    arrow = tk.Label(top, text="▸", bg=PANEL2, fg=DIM, font=F_SM)
    arrow.pack(side="right", anchor="n", padx=(0, px(8)))

    more = tk.Frame(box, bg=PANEL2)       # unfolded on click
    body = f.text + ((" " + f.detail) if f.detail else "")
    tk.Label(more, text=body, bg=PANEL2, fg=DIM, font=F_XS, justify="left",
             anchor="w", wraplength=px(440)).pack(fill="x",
                                                  padx=(px(45), 0))
    if f.tip:
        tk.Label(more, text="→ " + f.tip, bg=PANEL2, fg=color, font=F_XS,
                 justify="left", anchor="w", wraplength=px(440)).pack(
                     fill="x", padx=(px(45), 0), pady=(px(2), 0))
    more.bind("<Configure>", lambda e: [
        c.configure(wraplength=max(e.width - px(50), 120))
        for c in more.winfo_children()])

    def toggle(_=None):
        if more.winfo_ismapped():
            more.pack_forget()
            arrow.configure(text="▸")
        else:
            more.pack(fill="x", pady=(px(4), 0))
            arrow.configure(text="▾")
    for w in [box, top, mid, ic, chip, arrow] + list(mid.winfo_children()):
        w.bind("<Button-1>", toggle)
        w.configure(cursor="hand2")
    if app is not None:   # on every part, so moving inside doesn't flicker
        tip_text = (f.tip and ("%s\n→ %s" % (f.text, f.tip))) or f.text
        for w in [box, top, mid, ic, chip, arrow] + list(mid.winfo_children()):
            app.tooltip.bind(w, tip_text)


def clear(frame):
    for w in frame.winfo_children():
        w.destroy()


def fit_wrap(label, pad=4, minimum=120):
    """Wrap a label at its real width (pack it with fill="x"): a fixed
    wraplength cut long verdicts off in narrow windows and wrapped them
    needlessly early in wide ones."""
    def on(e):
        w = max(e.width - pad, minimum)
        if int(label.cget("wraplength") or 0) != w:
            label.configure(wraplength=w)
    label.bind("<Configure>", on, add="+")
    return label


def empty_note(parent, text, bg=PANEL):
    lab = tk.Label(parent, text=text, bg=bg, fg=DIM, font=F_SM,
                   justify="left", anchor="w", wraplength=px(480))
    lab.pack(fill="x", pady=SP_S)
    fit_wrap(lab)


def stat_text(key, v):
    return fmt_value(key, v)


def radar_axes(radar):
    """analysis.Radar -> theme RadarAxis list (with tooltip sub-rows)."""
    from ...theme.radar import RadarAxis
    return [RadarAxis(a.axis, a.label, a.score, a.lo, a.hi, a.n,
                      [(s.label, s.score, s.value_text, s.peer_text, s.n)
                       for s in a.subs]) for a in radar.axes]


def score_pm(radar) -> str:
    if radar is None or math.isnan(radar.overall_lo):
        return ""
    return "±%.0f" % ((radar.overall_hi - radar.overall_lo) / 2)


def grid(parent, n=2, pady=(0, 0), bg=DARK_BG):
    """A row of ``n`` equal-width columns (uniform grid). Returns frames."""
    f = tk.Frame(parent, bg=bg)
    f.pack(fill="x", pady=pady)
    cols = []
    for i in range(n):
        c = tk.Frame(f, bg=bg)
        c.grid(row=0, column=i, sticky="nsew",
               padx=(px(8) if i else 0, 0))
        f.grid_columnconfigure(i, weight=1, uniform="col")
        cols.append(c)
    f.grid_rowconfigure(0, weight=1)
    return cols


def big_number(parent, label, value, color=FG, sub="", bg=PANEL):
    """Caps caption, large number, dim sub-line (header blocks)."""
    from ...theme.tokens import F_LG_B
    f = tk.Frame(parent, bg=bg)
    tk.Label(f, text=label, bg=bg, fg=DIM, font=F_XS_B).pack(anchor="w")
    tk.Label(f, text=value, bg=bg, fg=color, font=F_LG_B).pack(anchor="w")
    if sub:
        tk.Label(f, text=sub, bg=bg, fg=DIM, font=F_XS).pack(anchor="w")
    return f


def stat_strip(parent, items, bg=PANEL):
    """Horizontal row of big_number blocks: [(label, value, colour, sub)]."""
    f = tk.Frame(parent, bg=bg)
    f.pack(fill="x")
    for i, (label, value, color, sub) in enumerate(items):
        big_number(f, label, value, color, sub, bg).pack(
            side="left", padx=(0 if i == 0 else px(26), 0))
    return f


def mean_pm(key, m) -> str:
    """'+312 ±140' style text for a Mean of a metric."""
    if not m.n or math.isnan(m.mean):
        return "—"
    s = fmt_value(key, m.mean)
    if math.isnan(m.se):
        return s
    h = fmt_value(key, m.half).lstrip("+")
    return "%s ±%s" % (s, h)


def rate_pm(r: Rate) -> str:
    return "%s %s" % (wr_text(r), pm_text(r)) if r.n else "—"


def date_text(ms: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ms / 1000).strftime("%d %b %H:%M")


def dur_text(s: int) -> str:
    return "%d:%02d" % (s // 60, s % 60)


def avg_word(res) -> str:
    """'Master+ avg' / 'peer avg' — label of the "where you should be"
    reference line for the comparison group picked under VS."""
    return "%s avg" % (getattr(res, "reference_label", None) or "peer")


def avg_strip(parent, res, items, bg=PANEL):
    """One line of comparison-group averages above a table:
    [(label, metric key or None, formatted value or None)]."""
    from ...analysis import fmt_value
    pm = getattr(res, "peer_means", {}) or {}
    parts = []
    for label, key, text in items:
        if text is None:
            if key not in pm:
                continue
            text = fmt_value(key, pm[key])
        parts.append("%s %s" % (label, text))
    if not parts:
        return
    f = tk.Frame(parent, bg=bg)
    f.pack(fill="x", pady=(0, px(6)))
    tk.Label(f, text=avg_word(res).upper() + "  ", bg=bg, fg=EDGE_HI,
             font=F_XS_B).pack(side="left")
    tk.Label(f, text="   ·   ".join(parts), bg=bg, fg=FG,
             font=F_SM).pack(side="left")


def peer_word(res, role=None) -> str:
    """Singular comparison-group noun for one role: 'Bot player', or
    'Master+ Bot player' when a benchmark is active."""
    from ...analysis import ROLE_LABEL
    r = ROLE_LABEL.get(role, "") if role else ""
    lab = getattr(res, "reference_label", None)
    words = [w for w in (lab, r, "player") if w]
    return " ".join(words)


def group_name(res) -> str:
    """Short name of the comparison group: 'peers' or 'Master+'."""
    return getattr(res, "reference_label", None) or "peers"


def fifty_means(res, role=None) -> str:
    """'50 = a typical Bot player in your games' /
    '50 = a typical Master+ Bot player'."""
    tail = "" if getattr(res, "reference_label", None) else " in your games"
    return "50 = a typical %s%s" % (peer_word(res, role), tail)
