"""Regression: a hover colour animation cancelled before its widget is
destroyed must not make destroy() fail ("can't delete Tcl command"), and a
screen switch / close must complete even if some widget's bookkeeping is
stale."""
import tkinter as tk

import pytest

from review.theme import anim


@pytest.fixture
def root():
    r = None
    for _ in range(3):       # Tk init can fail once while worker threads
        try:                 # from earlier tests are still winding down
            r = tk.Tk()
            break
        except tk.TclError as e:
            err = e
    if r is None:
        pytest.skip("no display: %s" % err)
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def test_cancelled_hover_animation_then_destroy(root):
    f = tk.Frame(root)
    lbl = tk.Label(f, bg="#101010")
    # a delayed tween is scheduled on the label, then a second hover stops
    # it (this used to cancel through the root window)
    anim.animate_wcol(lbl, "bg", "#ffffff", delay=500)
    anim.animate_wcol(lbl, "bg", "#202020", delay=500)
    anim.stop_wcol(lbl, "bg")
    f.destroy()                          # raised TclError before the fix


def test_safe_destroy_survives_stale_command_records(root):
    from review.ui.app import safe_destroy
    outer = tk.Frame(root)
    inner = tk.Frame(outer)
    lbl = tk.Label(inner)
    job = lbl.after(10_000, lambda: None)
    root.after_cancel(job)               # the old bug, reproduced on purpose
    with pytest.raises(tk.TclError):
        tk.Frame.destroy(tk.Frame(root))  # sanity: plain frames are fine
        outer.destroy()
    logged = []
    safe_destroy(outer, logged.append)
    assert not outer.winfo_exists()
