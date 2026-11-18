"""Analysis engine — pure, testable, no I/O.

    features = [extract_match(match, timeline, legendary) …]
    result = analyze(features, puuid, Filter(role="BOTTOM"))
"""
from .engine import AnalysisResult, Filter, analyze
from .extract import FEATURE_VERSION, extract_match
from .metrics import METRICS, fmt_value
from .model import ROLE_LABEL, ROLES, MatchFeatures, PlayerGame
from .radar import AXES, Radar, RadarConfig, load_config

__all__ = ["analyze", "Filter", "AnalysisResult", "extract_match",
           "FEATURE_VERSION", "METRICS", "fmt_value", "ROLES", "ROLE_LABEL",
           "MatchFeatures", "PlayerGame", "AXES", "Radar", "RadarConfig",
           "load_config"]
