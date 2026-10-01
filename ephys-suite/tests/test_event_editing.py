import numpy as np
import pytest

from ephys_suite import events as E
from ephys_suite.common import ts_ms
from test_synthetic import synth_events


def weak_event_trace(amp=-4.0, at=3000.0, noise=4.0, seed=3):
    t, y, times, a = synth_events(noise=noise, n=20, seed=seed)
    x = t - at
    m = x >= 0
    y = y.copy()
    y[m] += amp * (1 - np.exp(-x[m] / 0.6)) * np.exp(-x[m] / 6) / 0.55
    return t, y


def session(y, t, **kw):
    s = E.EventSession(t, y[None, :], E.EventParams(**kw))
    s.detect()
    return s


def test_rms_is_the_default_lower_threshold():
    assert E.EventParams().lower_mode == "rms"


def test_rms_excludes_omit_periods():
    t, y, *_ = synth_events(noise=4.0, n=5)
    y = y.copy()
    y[100:3000] += 800.0                                     # a test-pulse-like artefact
    a = E._lower_threshold_line(t, y, E.EventParams(omit=()))[0]
    b = E._lower_threshold_line(t, y, E.EventParams(omit=((0.0, 160.0),)))[0]
    assert abs(b) < 15 and abs(a) > 100


def test_fit_on_rejects_subthreshold_event_fit_off_accepts():
    t, y = weak_event_trace()
    s = session(y, t, lower_mode="linear", lower_value=-30.0)
    pk = s.snap_peak(0, 3001.5, half_ms=3)
    n0 = len(s.events)
    ev, why = s.add(0, pk, fit=True)
    assert ev is None and why in ("lower_threshold", "amplitude_threshold", "no_baseline")
    assert len(s.events) == n0                               # nothing changed on failure
    ev, why = s.add(0, pk, fit=False)
    assert why == "ok" and ev.manual and not ev.fitted and np.isfinite(ev.amplitude) and np.isnan(ev.rise_ms)
    assert len(s.events) == n0 + 1


def test_manual_baseline_when_auto_baseline_is_missing():
    t, y = weak_event_trace()
    s = session(y, t, lower_mode="linear", lower_value=-30.0)
    pk = s.snap_peak(0, 3001.5, half_ms=3)
    # a baseline after the peak is rejected; a baseline before the peak works and defines the amplitude exactly
    assert s.add(0, pk, fit=False, baseline=(pk + 5, 0.0))[1] == "bad_baseline"
    ev, why = s.add(0, pk, fit=False, baseline=(pk - 20, -1.0))
    assert why == "ok" and ev.baseline_manual and ev.bl_im == pytest.approx(-1.0)
    assert ev.amplitude == pytest.approx(y[pk] + 1.0)


def test_fit_on_with_manual_baseline_measures_kinetics():
    t, y, times, amp = synth_events(noise=0.5, n=10, amps=(-50, -50))
    s = session(y, t)
    e0 = s.events[0]
    s.delete(e0)
    ev, why = s.add(0, e0.peak_idx, fit=True, baseline=(e0.peak_idx - 40, float(np.mean(y[e0.peak_idx - 60:e0.peak_idx - 40]))))
    assert why == "ok" and ev.fitted and np.isfinite(ev.rise_ms) and np.isfinite(ev.tau_ms)


def test_delete_undo_redo():
    t, y, *_ = synth_events(noise=2.0, n=10)
    s = session(y, t)
    n = len(s.events)
    e = s.events[2]
    s.delete(e)
    assert len(s.events) == n - 1 and s.find_event(0, e.peak_idx) is None
    assert s.undo() and len(s.events) == n
    assert s.redo() and len(s.events) == n - 1
    assert not s.redo()


def test_edits_survive_redetection_and_can_be_cleared():
    t, y = weak_event_trace()
    s = session(y, t, lower_mode="linear", lower_value=-30.0)
    pk = s.snap_peak(0, 3001.5, half_ms=3)
    s.add(0, pk, fit=False)
    gone = s.events[0]
    s.delete(gone)
    n = len(s.events)
    s.p.deconv_sd = 3.5
    s.detect(keep_edits=True)
    assert len(s.events) == n and s.find_event(0, pk).manual and s.find_event(0, gone.peak_idx) is None
    s.detect(keep_edits=False)
    assert s.find_event(0, gone.peak_idx) is not None and (s.find_event(0, pk) is None or not s.find_event(0, pk).manual)


def test_set_baseline_remeasures_and_is_undoable():
    t, y, *_ = synth_events(noise=0.5, n=10, amps=(-40, -40))
    s = session(y, t)
    e = s.events[1]
    old = e.bl_im
    ne, why = s.set_baseline(e, e.peak_idx - 100, float(y[e.peak_idx - 100] + 3.0))
    assert why == "ok" and ne.baseline_manual and ne.bl_im != old
    s.undo()
    assert not s.find_event(0, e.peak_idx).baseline_manual


def test_snap_peak_drag_and_click():
    t, y, times, amp = synth_events(noise=0.2, n=5, amps=(-40, -40))
    s = session(y, t)
    e = s.events[0]
    assert s.snap_peak(0, e.peak_t - 1.0, half_ms=3) == e.peak_idx
    assert s.snap_peak(0, e.peak_t - 3, e.peak_t + 4) == e.peak_idx


def test_duplicate_add_is_reported_not_duplicated():
    t, y, *_ = synth_events(noise=2.0, n=10)
    s = session(y, t)
    e = s.events[0]
    ev, why = s.add(0, e.peak_idx, fit=False)
    assert why == "duplicate" and len(s.events) == len([x for x in s.events])
