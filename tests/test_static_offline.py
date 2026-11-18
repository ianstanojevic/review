"""The UI thread must never wait on Data Dragon: offline lookups read only
local files and return empty/None when nothing is cached."""
import json
import os
import time

import pytest

from review import config, static


@pytest.fixture
def ddir(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_DATA_DIR", str(tmp_path))
    config._dotenv.cache_clear()
    monkeypatch.setattr(static, "_CHAMPS", None)

    def no_network(*a, **k):
        raise AssertionError("network call on an offline path")
    monkeypatch.setattr(static.urllib.request, "urlopen", no_network)
    yield tmp_path / "ddragon"
    config._dotenv.cache_clear()


def test_offline_with_nothing_cached(ddir):
    assert static.latest_version(offline=True) is None
    assert static.legendary_ids(offline=True) == frozenset()
    assert static.champions(offline=True) == {}
    assert static.minimap(offline=True) is None
    # champion_name falls back to the internal name, no download
    assert static.champion_name("MonkeyKing") == "MonkeyKing"


def test_offline_uses_stale_local_files(ddir):
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "versions.json").write_text(json.dumps(["16.22.1"]))
    old = time.time() - 7 * 86400          # a week old: online would refetch
    os.utime(ddir / "versions.json", (old, old))
    (ddir / "champion_16.22.1.json").write_text(json.dumps({
        "MonkeyKing": {"name": "Wukong", "image": {"full": "MonkeyKing.png"}}}))
    assert static.latest_version(offline=True) == "16.22.1"
    assert static.champion_name("MonkeyKing") == "Wukong"
