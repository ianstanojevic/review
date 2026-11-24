"""API key lookup: environment > key saved in Settings > bundled .env."""
import pytest

from review import config


@pytest.fixture
def datadir(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("REVIEW_RIOT_API_KEY", raising=False)
    config._dotenv.cache_clear()
    yield tmp_path
    getattr(config._dotenv, "cache_clear", lambda: None)()


def test_saved_key_wins_over_bundled(datadir, monkeypatch):
    monkeypatch.setattr(config, "_dotenv",
                        lambda: {"REVIEW_RIOT_API_KEY": "RGAPI-bundled"})
    assert config.api_key() == "RGAPI-bundled"
    config.save_key("  RGAPI-pasted  ")
    assert (datadir / config.KEY_FILE).read_text() == "RGAPI-pasted"
    assert config.api_key() == "RGAPI-pasted"
    config.save_key("")                     # clearing falls back again
    assert config.api_key() == "RGAPI-bundled"


def test_environment_wins_over_saved(datadir, monkeypatch):
    config.save_key("RGAPI-pasted")
    monkeypatch.setenv("REVIEW_RIOT_API_KEY", "RGAPI-env")
    assert config.api_key() == "RGAPI-env"


def test_frozen_exe_reads_env_next_to_it(datadir, tmp_path, monkeypatch):
    """Review.exe (frozen) picks up the .env shipped beside it."""
    import sys
    app = tmp_path / "unzipped" / "Review"
    app.mkdir(parents=True)
    (app / ".env").write_text("REVIEW_RIOT_API_KEY=RGAPI-shipped\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "Review.exe"))
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "nowhere")
    monkeypatch.chdir(tmp_path)
    config._dotenv.cache_clear()
    assert config.setting("REVIEW_RIOT_API_KEY") == "RGAPI-shipped"
    assert config.api_key() == "RGAPI-shipped"


def test_built_in_key_used_when_nothing_saved(datadir, monkeypatch):
    import sys
    import types
    mod = types.ModuleType("review._bundled_key")
    mod.KEY = "RGAPI-builtin"
    monkeypatch.setitem(sys.modules, "review._bundled_key", mod)
    monkeypatch.setattr(config, "_dotenv",
                        lambda: {"REVIEW_RIOT_API_KEY": "RGAPI-envfile"})
    assert config.api_key() == "RGAPI-builtin"
    config.save_key("RGAPI-pasted")          # Settings still overrides
    assert config.api_key() == "RGAPI-pasted"


def test_masked_built_in_key(datadir, monkeypatch):
    import base64
    import os
    import sys
    import types
    raw = b"RGAPI-masked-1234"
    pad = os.urandom(len(raw))
    mod = types.ModuleType("review._bundled_key")
    mod.P = base64.b85encode(pad).decode()
    mod.M = base64.b85encode(bytes(a ^ b for a, b in zip(raw, pad))).decode()
    monkeypatch.setitem(sys.modules, "review._bundled_key", mod)
    monkeypatch.setattr(config, "_dotenv", lambda: {})
    assert "RGAPI" not in mod.P + mod.M
    assert config.api_key() == "RGAPI-masked-1234"


def test_key_source_order(tmp_path, monkeypatch):
    from review import config
    monkeypatch.setenv("REVIEW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("REVIEW_RIOT_API_KEY", raising=False)
    monkeypatch.setattr(config, "built_in_key", lambda: "RGAPI-built")
    monkeypatch.setattr(config, "setting", lambda *a, **k: None)
    monkeypatch.setattr(config, "_windows_user_env", lambda *a: None)
    assert config.key_source() == "built_in"
    config.save_key("RGAPI-mine")
    assert config.key_source() == "settings"
    monkeypatch.setenv("REVIEW_RIOT_API_KEY", "RGAPI-env")
    assert config.key_source() == "env"
