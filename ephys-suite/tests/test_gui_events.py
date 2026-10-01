"""Drives the Events tab the way a user would (offscreen)."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
try:
    from PySide6 import QtWidgets
    from ephys_suite.gui import MainWindow
except Exception as e:                                       # pragma: no cover (missing system GL libs)
    pytest.skip(f"Qt unavailable: {e}", allow_module_level=True)

from ephys_suite.io import Recording
from test_synthetic import synth_events


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app):
    t, y, times, amp = synth_events(noise=3.0, n=25, dur_s=6)
    y2 = y.copy()
    # one clear but weak event the default thresholds miss
    x = t - 4500.0
    m = x >= 0
    y2[m] += -22 * (1 - np.exp(-x[m] / 0.6)) * np.exp(-x[m] / 6) / 0.55
    rec = Recording("synthetic.abf", t, [np.vstack([y2, y2])], ["pA"], ["IN 0"], np.array([0.0, 6000.0]), None, "", 20000.0)
    w = MainWindow()
    w.rec = rec
    w.events.settings.clear()
    w.events.set_recording(rec)
    w.events.p.lower_mode = "linear"
    w.events.p.lower_value = -40.0                           # makes the weak event sub-threshold
    w.events.run()
    w.show()
    yield w, w.events, t, y2
    w.close()


def test_default_lower_threshold_is_rms(app):
    w = MainWindow()
    assert w.events.p.lower_mode == "rms"


def test_fit_on_rejects_fit_off_adds_and_hint_explains(win):
    w, ev, t, y = win
    pk = ev.session.snap_peak(0, 4501.5, half_ms=3)
    ev.btn_add.setChecked(True)
    ev.btn_fit.setChecked(True)
    n = len(ev.session.events)
    ev.on_click(t[pk], y[pk], ev.vb)
    assert len(ev.session.events) == n and "Fit kinetics" in ev.hint.text()
    ev.btn_fit.setChecked(False)
    ev.on_click(t[pk], y[pk], ev.vb)
    e = ev.session.find_event(0, pk)
    assert len(ev.session.events) == n + 1 and e.manual and not e.fitted


def test_manual_baseline_flow_switches_itself_off(win):
    w, ev, t, y = win
    pk = ev.session.snap_peak(0, 4501.5, half_ms=3)
    ev.btn_add.setChecked(True)
    ev.btn_fit.setChecked(False)
    ev.btn_bl.setChecked(True)
    ev.on_click(t[pk], y[pk], ev.vb)
    assert ev.pending == (0, pk) and ev.btn_bl.isChecked()
    ev.on_click(t[pk] + 5, 0.0, ev.vb)                       # baseline to the RIGHT of the peak -> refused, still waiting
    assert ev.pending == (0, pk) and ev.btn_bl.isChecked()
    ev.on_click(t[pk] - 8, 1.5, ev.vb)
    e = ev.session.find_event(0, pk)
    assert ev.pending is None and not ev.btn_bl.isChecked() and ev.btn_add.isChecked()
    assert e.baseline_manual and e.bl_im == pytest.approx(1.5)


def test_auto_prompt_for_baseline_when_none_found(win):
    w, ev, t, y = win
    first = ev.session.events[0]
    ev.session.delete(first)
    ev.p.baseline_search_ms = 0.01                            # no samples to search -> auto baseline impossible
    ev.btn_add.setChecked(True)
    ev.btn_fit.setChecked(False)
    ev.on_click(first.peak_t, first.peak_im, ev.vb)
    assert ev.pending is not None and ev.btn_bl.isChecked() and "baseline" in ev.hint.text().lower()
    ev.cancel()
    assert ev.pending is None and not ev.btn_bl.isChecked()


def test_delete_needs_two_clicks_in_normal_mode_and_undo(win):
    w, ev, t, y = win
    e = ev.session.events[3]
    n = len(ev.session.events)
    ev.btn_add.setChecked(False)
    ev.on_click(e.peak_t, e.peak_im, ev.vb)
    assert ev.sel is e and len(ev.session.events) == n
    ev.on_click(e.peak_t, e.peak_im, ev.vb)
    assert len(ev.session.events) == n - 1
    ev._undo(True)
    assert len(ev.session.events) == n


def test_add_mode_click_on_marker_does_not_delete(win):
    w, ev, t, y = win
    e = ev.session.events[2]
    n = len(ev.session.events)
    ev.btn_add.setChecked(True)
    ev.on_click(e.peak_t, e.peak_im, ev.vb)
    ev.on_click(e.peak_t, e.peak_im, ev.vb)
    assert len(ev.session.events) == n


def test_view_controls_and_paging_keep_scale(win):
    w, ev, t, y = win
    ev.vw.setValue(800)
    ev.vy0.setValue(-90)
    ev.vy1.setValue(30)
    ev.chk_locky.setChecked(True)
    x0 = ev.vx.value()
    ev.page(+1)
    assert ev.vx.value() == pytest.approx(x0 + 0.9 * 800, abs=1)
    assert ev.vw.value() == pytest.approx(800, abs=1) and (ev.vy0.value(), ev.vy1.value()) == pytest.approx((-90, 30), abs=0.5)
    for _ in range(12):                                       # run off the end of record 0 into record 1
        ev.page(+1)
    assert ev.rec_i == 1 and ev.vw.value() == pytest.approx(800, abs=1)
    ev.chk_locky.setChecked(False)
    ev.page(+1)
    assert ev.vy1.value() != 30                               # Y fitted to the visible data


def test_remembered_view_is_reapplied(win):
    w, ev, t, y = win
    ev.vw.setValue(1234)
    ev.vy0.setValue(-55)
    ev.vy1.setValue(22)
    ev.remember_view()
    ev.vw.setValue(100)
    ev._apply_saved_view()
    assert ev.vw.value() == pytest.approx(1234, abs=1) and ev.vy0.value() == pytest.approx(-55, abs=0.5)
    ev.settings.clear()


def test_edits_survive_rerun_and_are_discardable(win):
    w, ev, t, y = win
    pk = ev.session.snap_peak(0, 4501.5, half_ms=3)
    ev.btn_add.setChecked(True)
    ev.btn_fit.setChecked(False)
    ev.on_click(t[pk], y[pk], ev.vb)
    gone = ev.session.events[0]
    ev.sel = gone
    ev.delete_selected()
    ev.run()
    assert ev.session.find_event(0, pk).manual and ev.session.find_event(0, gone.peak_idx) is None
    ev.session.log = type(ev.session.log)()                  # discard
    ev.session._rebuild()
    assert ev.session.find_event(0, gone.peak_idx) is not None


def test_default_window_is_not_reset_by_the_data(win):
    w, ev, t, y = win
    ev.settings.clear()
    ev._new_session()
    assert ev.vw.value() == pytest.approx(1000, abs=1)       # default 1 s window, not the whole 6 s record
    x0 = ev.vx.value()
    ev.page(+1)
    assert ev.vx.value() == pytest.approx(x0 + 900, abs=1) and ev.rec_i == 0
