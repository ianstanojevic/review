"""Fixed vocabularies for tagging. Fixed on purpose: free-form tags can't
be counted, and counting is the whole point (Review shows the totals)."""
from __future__ import annotations

# kind of a moment on the timeline
DEATH, MISSED, BOOKMARK = "death", "missed", "bookmark"
KINDS = {DEATH: "Death", MISSED: "Missed chance", BOOKMARK: "Bookmark"}

DEATH_CATEGORIES = [
    ("position", "Positioning"),
    ("vision", "No vision / info"),
    ("overextend", "Overextended"),
    ("greed", "Greed"),
    ("trade", "Bad trade / all-in"),
    ("gank", "Avoidable gank"),
    ("mechanics", "Mechanics"),
    ("forced", "Forced by team"),
    ("worth", "Worth it"),
]

MISSED_CATEGORIES = [
    ("wave", "Left a wave"),
    ("trade", "Skipped a trade"),
    ("recall", "Bad recall"),
    ("objective", "Missed objective"),
    ("plates", "Missed plates"),
    ("rotate", "Didn't rotate"),
    ("other", "Other"),
]

CATEGORIES = {DEATH: DEATH_CATEGORIES, MISSED: MISSED_CATEGORIES,
              BOOKMARK: []}
CATEGORY_LABEL = {(k, key): lab for k, cats in CATEGORIES.items()
                  for key, lab in cats}

# avoidable: 1 yes, 0 no, None not answered
AVOIDABLE = [(1, "Yes"), (0, "No")]

LANE_RATING = [(1, "Lost hard"), (2, "Lost"), (3, "Even"), (4, "Won"),
               (5, "Won hard")]


def category_label(kind: str, key: str | None) -> str:
    if not key:
        return ""
    return CATEGORY_LABEL.get((kind, key), key)


def fmt_time(seconds: float) -> str:
    s = max(int(round(seconds)), 0)
    return "%d:%02d" % (s // 60, s % 60)
