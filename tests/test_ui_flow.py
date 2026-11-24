"""UI smoke test: a result arrives, every view is built behind the reveal
curtain and shown — with no exception in any Tk callback. Offline: all
network calls fail fast; the data dir is a temp folder."""
import time
import tkinter as tk

import pytest

from analysis_helpers import ME, synth_games
from review import config
from review.analysis import Filter, analyze, load_config


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_DATA_DIR", str(tmp_path))
    config._dotenv.cache_clear()
    import review.static as static

    def offline(*a, **k):
        raise OSError("offline in tests")
    monkeypatch.setattr(static.urllib.request, "urlopen", offline)
    monkeypatch.setattr(static, "_CHAMPS", None)
    from review.ui.app import App
    # image caches belong to a Tk interpreter: start each app clean
    import review.theme.roleicons as roleicons
    import review.ui.screens.base as base
    roleicons._CACHE.clear()
    base._ICON_CACHE.clear()
    a = None
    for _ in range(3):       # see test_ui_destroy: Tk init can fail once
        try:
            a = App()
            break
        except tk.TclError as e:
            err = e
    if a is None:
        pytest.skip("no display: %s" % err)
    a.exit_on_close = False
    a.root.geometry("1200x860+-6000+20")
    a.errors = []
    a.root.report_callback_exception = lambda e, v, tb: a.errors.append(v)
    a._log = lambda e: a.errors.append(e)
    yield a
    try:
        a.root.destroy()
    except tk.TclError:
        pass
    config._dotenv.cache_clear()


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        time.sleep(0.01)


def curtains(app):
    return [w for w in app.content.winfo_children()
            if isinstance(w, tk.Canvas)]


def test_every_view_reveals_without_errors(app):
    from review.ui.app import VIEWS
    res = analyze(synth_games(40, boost=0.3), ME, Filter(), load_config(),
                  compute_ci=False)
    app.player = {"puuid": ME, "riot_id": "Test#T", "region": "EUW"}
    app._show_bars()
    app._show_busy("Analysing Test#T…")
    pump(app, 0.2)
    app._on_result(app.gen, res, None, False)
    pump(app, 1.5)
    assert not app.errors, app.errors
    assert not curtains(app), "curtain never lifted on Overview"
    for key, _ in VIEWS:
        app.navigate(key)
        pump(app, 1.2)
        assert not app.errors, (key, app.errors)
        assert not curtains(app), "curtain stuck on %s" % key
        assert app.screens[key].winfo_manager() == "grid"
    assert not app.errors, app.errors


def test_radar_grows_even_if_the_reveal_is_interrupted(app):
    """A second navigation during the gold sweep used to cut it short, and
    the radar stayed parked at its centre (every axis showing 20)."""
    res = analyze(synth_games(40, boost=0.3), ME, Filter(), load_config(),
                  compute_ci=False)
    app.player = {"puuid": ME, "riot_id": "Test#T", "region": "EUW"}
    app._show_bars()
    app._on_result(app.gen, res, None, False)
    end = time.monotonic() + 3
    while time.monotonic() < end and curtains(app):
        app.root.update()               # wait for the curtain to drop…
        time.sleep(0.005)
    app.navigate("overview")            # …and interrupt the sweep
    pump(app, 2.5)
    radar = app.screens["overview"].radar
    target = [a.score for a in radar.axes]
    assert radar.shown is not None
    assert max(abs(s - t) for s, t in zip(radar.shown, target)) < 0.5
    assert not app.errors, app.errors


@pytest.mark.parametrize("case", ["no_radar", "few_games"])
def test_every_view_copes_with_thin_results(app, case):
    """Games but no radar (no role from Riot) or only 3 games: every view
    must show something sensible, not raise."""
    from review.ui.app import VIEWS
    games = synth_games(30 if case == "no_radar" else 3, seed=21)
    if case == "no_radar":
        for m in games:
            m.player(ME).role = None
    res = analyze(games, ME, Filter(), load_config(), compute_ci=False)
    app.player = {"puuid": ME, "riot_id": "Test#T", "region": "EUW"}
    app._show_bars()
    app._on_result(app.gen, res, None, False)
    pump(app, 1.5)
    for key, _ in VIEWS:
        app.navigate(key)
        pump(app, 1.0)
        assert not app.errors, (case, key, app.errors)
