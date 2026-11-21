"""Screen registry: VIEW tab key -> Screen class (imported lazily)."""
from __future__ import annotations

import importlib


class _Registry(dict):
    _MAP = {
        "overview": ("overview", "OverviewScreen"),
        "lane": ("lane", "LaneScreen"),
        "champions": ("champions", "ChampionsScreen"),
        "trends": ("trends", "TrendsScreen"),
        "winloss": ("winloss", "WinLossScreen"),
        "deaths": ("deaths", "DeathsScreen"),
        "radar": ("radarview", "RadarScreen"),
        "compare": ("compare", "CompareScreen"),
        "lab": ("lab", "LabScreen"),
        "matches": ("matches", "MatchesScreen"),
    }

    def __missing__(self, key):
        mod, cls = self._MAP[key]
        m = importlib.import_module("%s.%s" % (__name__, mod))
        self[key] = getattr(m, cls)
        return self[key]


REGISTRY = _Registry()
