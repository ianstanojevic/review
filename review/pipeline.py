"""Glue between the cache and the pure analysis engine.

* Extracted features are cached per match (keyed by extractor version and
  the legendary-item set), so re-analysis doesn't re-parse timelines.
* Whole analysis results are cached by (engine version, player, filter,
  exact match set, radar config) — the bootstrap only reruns when something
  that affects it changes.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from typing import Iterable

from .analysis import (FEATURE_VERSION, AnalysisResult, Filter,
                       MatchFeatures, analyze, extract_match, load_config)
from .cache import Cache

ENGINE_VERSION = "5"


def _features_version(legendary: frozenset[int]) -> str:
    h = hashlib.sha1(json.dumps(sorted(legendary)).encode()).hexdigest()[:8]
    return "%s-%s" % (FEATURE_VERSION, h)


def load_features(cache: Cache, match_ids: Iterable[str],
                  legendary: frozenset[int] = frozenset()) -> list[MatchFeatures]:
    ids = list(match_ids)
    ver = _features_version(legendary)
    have = cache.get_features(ids, ver)
    out, new = [], []
    for mid in ids:
        if mid in have:
            out.append(MatchFeatures.from_dict(have[mid]))
            continue
        match = cache.get_match(mid)
        if match is None:
            continue
        f = extract_match(match, cache.get_timeline(mid), legendary)
        out.append(f)
        new.append((mid, f.to_dict()))
    if new:
        cache.put_features(new, ver)
    return out


def analyze_player(cache: Cache, puuid: str, flt: Filter = Filter(),
                   legendary: frozenset[int] = frozenset(),
                   use_cache: bool = True,
                   limit: int | None = None,
                   reference: tuple[str, str] | None = None
                   ) -> AnalysisResult:
    """``limit``: analyse only the player's most recent N cached games.
    ``reference``: (region, level) of a benchmark to score against, e.g.
    ("EUW", "master"); None = your own games' players."""
    cfg = load_config()
    ids = cache.player_match_ids(puuid, flt.queues, limit)
    ref_ids = cache.benchmark_ids(*reference) if reference else []
    key = hashlib.sha256(json.dumps([
        ENGINE_VERSION, _features_version(legendary), puuid, flt.key(),
        sorted(ids), cfg.fingerprint(), list(reference or ()),
        hashlib.sha1("".join(ref_ids).encode()).hexdigest()]).encode()
    ).hexdigest()
    if use_cache:
        blob = cache.get_blob(key)
        if blob is not None:
            try:
                return pickle.loads(blob)
            except Exception:
                pass          # stale pickle after a code change: recompute
    feats = load_features(cache, ids, legendary)
    ref_feats = label = None
    if reference:
        from .benchmark import LEVELS
        ref_feats = load_features(cache, ref_ids, legendary)
        label = LEVELS[reference[1]].label
    res = analyze(feats, puuid, flt, cfg, reference=ref_feats,
                  reference_label=label)
    cache.put_blob(key, pickle.dumps(res))
    return res
