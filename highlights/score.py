"""Find and score your best moments in a game. Pure logic, no UI, no I/O.

In: a ``GameView`` from ``replay.events.build_game`` (kills, assists,
objectives and towers with who took part). Out: ``Highlight`` clips, each a
time window (a few seconds before the first thing you did until a few
seconds after the last), a title and a score.

Scoring (points per "beat", one beat = one thing you did):

* kill 10 · solo +8 · on your lane opponent +5 (solo on them: +5 more)
* shutdown: +4, +2 per 100 g of shutdown bounty (max +14)
* behind the victim in gold: +5 from 1000 g, +10 from 2500 g
* multikill on top: double +15, triple +35, quadra +60, penta +100
  (League's windows: 10 s between kills, 30 s from quadra to penta)
* assist 3 (+2 on a shutdown)
* objectives you took part in (last hit or assist): Baron 18, Elder 22,
  Atakhan 14, Dragon 8, Herald 8, Grubs 5 (+4 if you last-hit it);
  tower 4, inhibitor 6
* a clip with 3+ takedowns of yours is a teamfight: +8

Beats less than ``MERGE_GAP_MS`` apart become one clip (a teamfight and
the Baron after it), up to ``MAX_CLIP_MS``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PRE_MS = 8_000            # lead-up before the first beat
POST_MS = 4_000           # after the last one
MERGE_GAP_MS = 15_000     # beats closer than this share a clip
MAX_CLIP_MS = 75_000      # never longer than this (recording is real time)
MULTI_GAP_MS = 10_000     # League's multikill window
PENTA_GAP_MS = 30_000     # quadra -> penta

MULTI = {2: ("Double kill", "double", 15), 3: ("Triple kill", "triple", 35),
         4: ("Quadra kill", "quadra", 60), 5: ("Penta kill", "penta", 100)}
OBJECTIVE_POINTS = {"Baron": 18, "Elder": 22, "Atakhan": 14, "Herald": 8,
                    "Grubs": 5}
DRAGON_POINTS = 8
TOWER_POINTS, INHIB_POINTS = 4, 6
TEAMFIGHT_TAKEDOWNS, TEAMFIGHT_BONUS = 3, 8

# which slug names the clip (file name), most special first
KIND_ORDER = ["penta", "quadra", "triple", "double", "elder", "baron",
              "shutdown", "solokill", "teamfight", "atakhan", "kill",
              "dragon", "herald", "grubs", "assist", "inhib", "tower"]


@dataclass
class Beat:
    t_ms: int
    kind: str                 # kill | assist | objective | tower
    points: int
    label: str
    tags: set[str] = field(default_factory=set)
    victim: int | None = None


@dataclass
class Highlight:
    start_ms: int
    end_ms: int
    score: int
    title: str
    kind: str                 # slug for the file name: triple, baron, …
    beats: list[Beat] = field(default_factory=list)

    @property
    def t_ms(self) -> int:
        """When the first thing you did happens."""
        return self.beats[0].t_ms if self.beats else self.start_ms

    @property
    def length_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000

    @property
    def minute(self) -> int:
        return self.t_ms // 60_000

    @property
    def filename(self) -> str:
        return "%dm_%s.webm" % (self.minute, self.kind)

    @property
    def details(self) -> str:
        return " · ".join(b.label for b in self.beats)


# --------------------------------------------------------------- beats

def _kill_beats(g, name_of) -> list[Beat]:
    me = g.me.pid
    lane = {p.pid for p in (g.opponent, g.enemy_support) if p}
    if g.me.role == "UTILITY" and g.opponent:
        lane = {g.opponent.pid} | ({g.enemy_support.pid} if g.enemy_support
                                   else set())
    out = []
    for m in g.moments:
        if m.kind not in ("kill", "assist"):
            continue
        d = m.detail or {}
        victim = d.get("victim")
        name = name_of(g.champ(victim) or "?")
        shut = int(d.get("shutdown") or 0)
        if m.kind == "assist" or d.get("killer", me) != me:
            pts = 3 + (2 if shut else 0)
            out.append(Beat(m.t_ms, "assist", pts, "Assist on %s" % name,
                            {"assist"}, victim))
            continue
        pts, tags = 10, {"kill"}
        bits = []
        solo = not d.get("assists")
        on_lane = victim in lane
        if solo:
            pts += 8
            tags.add("solokill")
        if on_lane:
            pts += 5 + (5 if solo else 0)
            tags.add("lane")
        if shut:
            pts += min(4 + 2 * (shut // 100), 14)
            tags.add("shutdown")
            bits.append("%dg shutdown" % shut)
        gd = d.get("gold_diff")
        if gd is not None and gd <= -1000:
            pts += 10 if gd <= -2500 else 5
            tags.add("behind")
            bits.append("%.1fk behind" % (-gd / 1000))
        label = "%s %s" % ("Solo kill on" if solo else "Kill on", name)
        if on_lane and solo:
            label = "Solo kill on your laner %s" % name
        if bits:
            label += " (%s)" % ", ".join(bits)
        out.append(Beat(m.t_ms, "kill", pts, label, tags, victim))
    return out


def multikills(kill_times: list[int]) -> list[tuple[int, int]]:
    """[(index of the last kill, streak length ≥ 2)] for a sorted list of
    your kill times, with League's windows (10 s, 30 s quadra → penta)."""
    out, run = [], 1
    for i in range(1, len(kill_times) + 1):
        if i < len(kill_times):
            gap = kill_times[i] - kill_times[i - 1]
            window = PENTA_GAP_MS if run == 4 else MULTI_GAP_MS
            if gap <= window and run < 5:
                run += 1
                continue
        if run >= 2:
            out.append((i - 1, run))
        run = 1
    return out


def _objective_beats(g) -> list[Beat]:
    me = g.me.pid
    out = []
    for m in g.moments:
        d = m.detail or {}
        if m.kind not in ("objective", "tower") or not m.ours \
                or not d.get("mine"):
            continue
        name = d.get("name") or m.label
        last_hit = d.get("killer") == me
        if m.kind == "tower":
            inhib = name == "Inhibitor"
            out.append(Beat(m.t_ms, "tower", INHIB_POINTS if inhib
                            else TOWER_POINTS, name, {"inhib" if inhib
                                                      else "tower"}))
            continue
        if name.endswith("dragon"):
            pts, slug = DRAGON_POINTS, "dragon"
        else:
            pts = OBJECTIVE_POINTS.get(name, 5)
            slug = name.lower()
        if last_hit:
            pts += 4
        out.append(Beat(m.t_ms, "objective", pts, name + (
            " (your last hit)" if last_hit else ""), {slug}))
    return out


def beats(g, name_of=None) -> list[Beat]:
    """Every scored thing you did, in time order. ``name_of`` turns Riot's
    champion keys into display names ("MissFortune" -> "Miss Fortune")."""
    ks = _kill_beats(g, name_of or (lambda c: c))
    kills = [b for b in ks if b.kind == "kill"]
    for i, n in multikills([b.t_ms for b in kills]):
        name, slug, pts = MULTI[n]
        kills[i].points += pts
        kills[i].tags.add(slug)
        kills[i].label = "%s — %s" % (name, kills[i].label)
    return sorted(ks + _objective_beats(g), key=lambda b: b.t_ms)


# --------------------------------------------------------------- clips

def _clusters(bs: list[Beat]) -> list[list[Beat]]:
    out: list[list[Beat]] = []
    span = MAX_CLIP_MS - PRE_MS - POST_MS
    for b in bs:
        if out and b.t_ms - out[-1][-1].t_ms <= MERGE_GAP_MS \
                and b.t_ms - out[-1][0].t_ms <= span:
            out[-1].append(b)
        else:
            out.append([b])
    return out


def _title(bs: list[Beat], g) -> tuple[str, str]:
    tags = set().union(*(b.tags for b in bs))
    kills = [b for b in bs if b.kind == "kill"]
    assists = [b for b in bs if b.kind == "assist"]
    objs = [b for b in bs if b.kind in ("objective", "tower")]
    takedowns = len(kills) + len(assists)
    multi = next((MULTI[n] for n in (5, 4, 3, 2) if MULTI[n][1] in tags),
                 None)
    if multi:
        head = multi[0]
    elif takedowns >= TEAMFIGHT_TAKEDOWNS:
        head = "Teamfight: %s" % _count(len(kills), len(assists))
    elif len(kills) == 1 and not assists:
        head = kills[0].label
    elif len(kills) + len(assists) == 1:
        head = (kills or assists)[0].label
    elif kills and not assists:
        head = "Kills on " + " + ".join(b.label.split(" on ", 1)[1].split(
            " (")[0].replace("your laner ", "") for b in kills)
    elif kills or assists:
        head = _count(len(kills), len(assists))
    else:
        head = ""
    names = []
    for b in objs:
        n = b.label.replace(" (your last hit)", "")
        if n not in names:
            names.append(n)
    if head and names:
        title = "%s + %s" % (head, " + ".join(names))
    else:
        title = head or " + ".join(names)
    if takedowns >= TEAMFIGHT_TAKEDOWNS:
        tags = tags | {"teamfight"}
    slug = next((k for k in KIND_ORDER if k in tags), "moment")
    return title, slug


def _count(k: int, a: int) -> str:
    bits = []
    if k:
        bits.append("%d kill%s" % (k, "" if k == 1 else "s"))
    if a:
        bits.append("%d assist%s" % (a, "" if a == 1 else "s"))
    return ", ".join(bits)


def find_highlights(g, name_of=None) -> list[Highlight]:
    """Clips in time order. Empty without a timeline."""
    end_of_game = max(int(g.duration_s * 1000), 0)
    out = []
    for c in _clusters(beats(g, name_of)):
        score = sum(b.points for b in c)
        takedowns = sum(1 for b in c if b.kind in ("kill", "assist"))
        if takedowns >= TEAMFIGHT_TAKEDOWNS:
            score += TEAMFIGHT_BONUS
        title, slug = _title(c, g)
        start = max(c[0].t_ms - PRE_MS, 0)
        end = c[-1].t_ms + POST_MS
        if end_of_game:
            end = min(end, end_of_game)
        out.append(Highlight(start, max(end, start + 1000), score, title,
                             slug, c))
    return out


def default_selection(hs: list[Highlight], max_n: int = 5,
                      min_score: int = 15) -> set[int]:
    """Indices of the clips ticked from the start: the best ones, at most
    ``max_n``, and never weak ones next to a much better clip."""
    if not hs:
        return set()
    best = max(h.score for h in hs)
    bar = max(min_score, best * 0.3)
    ranked = sorted(range(len(hs)), key=lambda i: -hs[i].score)
    pick = {i for i in ranked[:max_n] if hs[i].score >= bar}
    return pick or {ranked[0]}


SETUP_S = 12      # per clip: seek, camera pans to you, encoder start/stop
LOAD_S = 45       # replay not open yet: download check, game load


def estimate_s(hs: list[Highlight], replay_open: bool = False) -> int:
    """Recording is real time: clip lengths + setup per clip (+ loading)."""
    if not hs:
        return 0
    return int(sum(h.length_s for h in hs) + SETUP_S * len(hs)
               + (0 if replay_open else LOAD_S))


def folder_name(g) -> str:
    """``2026-11-21_Kalista_vs_Vayne``."""
    return folder_for(g.created_ms, g.me.champion,
                      g.opponent.champion if g.opponent else None)


def folder_for(created_ms: int, champion: str, opponent: str | None) -> str:
    try:
        day = datetime.fromtimestamp(created_ms / 1000).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        day = "game"
    return _safe("%s_%s_vs_%s" % (day, champion, opponent or "unknown"))


def file_names(hs: list[Highlight]) -> list[str]:
    """One file name per clip, unique within the game folder."""
    seen: dict[str, int] = {}
    out = []
    for h in hs:
        n = h.filename
        k = seen.get(n, 0)
        seen[n] = k + 1
        out.append(n if not k else n.replace(".webm", "_%d.webm" % (k + 1)))
    return out


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)
