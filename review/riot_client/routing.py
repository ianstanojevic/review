"""Region routing and Riot ID parsing.

account-v1 and match-v5 live on the *regional* host (europe / americas /
asia / sea); league-v4 and summoner-v4 on the *platform* host (euw1, na1…).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    code: str       # what the user picks: "EUW"
    platform: str   # league-v4 / summoner-v4 host: "euw1"
    regional: str   # match-v5 host: "europe"

    @property
    def account_host(self) -> str:
        # account-v1 is served from americas/europe/asia only (not sea)
        return "asia" if self.regional == "sea" else self.regional


_TABLE = (
    ("EUW", "euw1", "europe"), ("EUNE", "eun1", "europe"),
    ("TR", "tr1", "europe"), ("RU", "ru", "europe"), ("ME", "me1", "europe"),
    ("NA", "na1", "americas"), ("BR", "br1", "americas"),
    ("LAN", "la1", "americas"), ("LAS", "la2", "americas"),
    ("KR", "kr", "asia"), ("JP", "jp1", "asia"),
    ("OCE", "oc1", "sea"), ("SG", "sg2", "sea"), ("TW", "tw2", "sea"),
    ("VN", "vn2", "sea"),
)
REGIONS: dict[str, Region] = {c: Region(c, p, r) for c, p, r in _TABLE}
PRIMARY_REGIONS = ("EUW", "EUNE", "NA", "KR")


def get_region(code: str) -> Region:
    try:
        return REGIONS[code.strip().upper()]
    except KeyError:
        raise ValueError("Unknown region %r. Supported: %s"
                         % (code, ", ".join(REGIONS))) from None


def region_for_platform(platform: str) -> Region | None:
    """'na1' -> Region NA (None when unknown)."""
    platform = (platform or "").strip().lower()
    for r in REGIONS.values():
        if r.platform == platform:
            return r
    return None


def clean_riot_id(text: str) -> str:
    """Tidy a typed or pasted Riot ID: op.gg & co. copy as 'Ryuk\n#adc',
    which ended up as a two-line name everywhere. Any run of whitespace
    becomes one space, and none is kept around the '#'."""
    s = " ".join((text or "").split())
    name, sep, tag = s.rpartition("#")
    return "%s#%s" % (name.strip(), tag.strip()) if sep else s


def parse_riot_id(text: str) -> tuple[str, str]:
    """'flintgnuggaren#ian' -> ('flintgnuggaren', 'ian').

    Game names may contain spaces and '#'-free unicode; the tag is after the
    LAST '#'.
    """
    name, sep, tag = clean_riot_id(text).rpartition("#")
    name, tag = name.strip(), tag.strip()
    if not sep or not name or not tag:
        raise ValueError("Riot ID must look like Name#TAG (got %r)" % text)
    if len(name) > 16 or len(tag) > 5:
        raise ValueError("Riot ID too long: names are ≤16 chars, tags ≤5")
    return name, tag
