"""Design tokens — copied verbatim from DRAFT (draft_tool_v2.py) so both
apps ship from the same studio. Change a value here only together with
DRAFT. REVIEW-only additions are at the bottom and clearly marked.
"""
from __future__ import annotations

# --- DRAFT: hextech palette (League design language) -----------------------
DARK_BG = "#010a13"    # abyss navy
PANEL = "#091020"      # panel base
PANEL2 = "#0d1b2e"     # raised panel / input field
BORDER = "#1e3247"     # steel-blue input border
EDGE = "#785a28"       # dark hextech gold, panel frames
EDGE_HI = "#c8aa6e"    # light gold
FG = "#f0e6d2"         # parchment text
DIM = "#a09b8c"        # muted gold-grey
ACCENT = "#c8aa6e"     # gold accent (buttons, active tab)
ACCENT_HI = "#e2ca92"  # lighter gold — hover glow on gold buttons
BTN_FG = "#0a1428"     # dark text on gold button
GOOD = "#0ac8b9"       # hextech teal
BAD = "#e84057"        # league red
WIN = "#5383e8"        # victory blue (match history W)
HOVER = "#16273e"      # highlighted row on mouse-over
ROW_ALT = "#0b1626"    # zebra row
TOP_BG = "#13233a"     # highlighted #1 row
DMG_AD = "#d16b4e"     # physical damage
DMG_AP = "#4a9fe0"     # magic damage
DMG_TRUE = "#e8e0d0"   # true damage
TEAL_BG = "#0a323c"    # active state of DRAFT's TIER tab row

TITLE_FONT = ("Georgia", 17, "bold")
SP_XS, SP_S, SP_M, SP_L, SP_XL = 2, 4, 8, 12, 16
UI = "Segoe UI"
SERIF = "Georgia"
F_XS = (UI, 8)
F_SM = (UI, 9)
F_MD = (UI, 10)
F_XS_B = (UI, 8, "bold")
F_SM_B = (UI, 9, "bold")
F_MD_B = (UI, 10, "bold")

# --- REVIEW additions -------------------------------------------------------
F_LG_B = (UI, 13, "bold")
F_NUM = (SERIF, 22, "bold")          # headline numbers (same serif as title)
GRID = "#16273e"                     # chart gridlines (= HOVER tone)
GRID_SOFT = "#0f1d31"
GHOST = "#7d8590"                    # previous period / baseline series
RING_A = "#0b1626"                   # radar ring fills (alternating)
RING_B = "#0d1b2e"

# Font files for off-screen (PIL) rendering of charts / PNG export.
FONT_FILES = {
    (UI, False): ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"),
    (UI, True): ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    (SERIF, False): ("georgia.ttf", "times.ttf", "DejaVuSerif.ttf"),
    (SERIF, True): ("georgiab.ttf", "timesbd.ttf", "DejaVuSerif-Bold.ttf"),
}


def hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(c1: str, c2: str, t: float) -> str:
    """Blend two hex colours; t = 0 -> c1, 1 -> c2."""
    a, b = hex_rgb(c1), hex_rgb(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t)
                                   for i in range(3))


def signed_color(v: float, good_if_positive: bool = True,
                 dead: float = 0.0) -> str:
    """Teal for good, red for bad, parchment within the dead zone."""
    if v != v or abs(v) <= dead:            # NaN or ~0
        return FG
    good = (v > 0) == good_if_positive
    return GOOD if good else BAD


def score_color(score: float) -> str:
    """0–100 score on DRAFT's palette: red ≤ 40 … parchment 50 … teal ≥ 60."""
    if score != score:
        return DIM
    if score >= 50:
        return mix(FG, GOOD, min((score - 50) / 15, 1.0))
    return mix(FG, BAD, min((50 - score) / 15, 1.0))
