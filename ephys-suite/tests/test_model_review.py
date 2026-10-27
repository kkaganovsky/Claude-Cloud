"""Review of classifier-proposed events in the Events tab (offscreen)."""
import csv
import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
try:
    from PySide6 import QtWidgets
    from ephys_suite.gui import MainWindow
except Exception as e:                                       # pragma: no cover
    pytest.skip(f"Qt unavailable: {e}", allow_module_level=True)

from ephys_suite import events as E
from ephys_suite import model_review as MR
from ephys_suite.io import Recording
from test_synthetic import synth_events


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def setup(app, tmp_path):
    t, y, times, amp = synth_events(noise=2.0, n=12, dur_s=5)
    rec = Recording("synthetic.abf", t, [y[None, :]], ["pA"], ["IN 0"], np.array([0.0]), None, "", 20000.0)
    w = MainWindow()
    w.rec = rec
    w.events.settings.clear()
    w.events.set_recording(rec)
    peaks = [float(tm) + 1.5 for tm in times]               # event peak ~1.5 ms after onset
    rows = [(p, 0.97, 1) for p in peaks[:-2]]
    rows.append((peaks[-2], 0.55, 0))                        # real event the model rejected (borderline)
    rows.append((peaks[-1], 0.99, 1))
    false_t = float(times[0]) + 40.0
    rows.append((false_t, 0.80, 1))                          # an accepted false positive (no event there)
    path = tmp_path / "cands.csv"
    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["sweep", "time_in_sweep_ms", "score", "accepted"])
        for p, s, a in rows:
            wr.writerow([1, p, s, a])
    yield w, w.events, str(path), peaks, false_t, tmp_path
    w.close()


def test_load_review_accept_delete_save(setup):
    w, ev, path, peaks, false_t, tmp = setup
    assert ev.load_candidates_file(path)
    n0 = len(ev.session.events)
    assert ev.session.external and n0 == len(peaks)         # every accepted candidate was measured
    st = ev.review
    assert len(st.cands) == len(peaks) + 1 and sum(c.accepted for c in st.cands) == len(peaks)
    # the false positive: select it and delete it
    fp = ev.session.find_event(0, ev.session.snap_peak(0, false_t, None, half_ms=1.0))
    if fp is not None:
        ev.select(fp)
        ev.delete_selected()
    # accept the borderline candidate (what a click on the orange ring does)
    bord = [c for c in st.cands if not c.accepted][0]
    ev.accept_candidate(bord)
    assert ev.session.find_event(0, bord.peak_idx) is not None
    # queue steps through the most uncertain first
    c = st.step(+1)
    assert c is not None and abs(MR._logit(c.score) - MR._logit(st.thr)) <= abs(MR._logit(st.queue[-1].score) - MR._logit(st.thr)) + 1e-9
    ev.mark_sweep_reviewed()
    assert 0 in st.reviewed
    out = tmp / "review.csv"
    n, changed = MR.export_review(str(out), ev.session, st, "synthetic.abf")
    rows = list(csv.DictReader(open(out)))
    by = {int(r["peak_idx"]): r for r in rows}
    assert by[bord.peak_idx]["final_event"] == "1" and by[bord.peak_idx]["model_accepted"] == "0"
    gone = [r for r in rows if r["model_accepted"] == "1" and r["final_event"] == "0"]
    assert len(gone) == 1                                    # the deleted false positive
    assert changed >= 2
    meta = json.load(open(tmp / "review.json"))
    assert meta["reviewed_sweeps"] == [1] and meta["n_changed"] == changed


def test_manual_add_is_exported_and_undo_works(setup):
    w, ev, path, peaks, false_t, tmp = setup
    ev.load_candidates_file(path)
    t = ev.session.t
    new_t = peaks[0] + 700.0
    pk = ev.session.snap_peak(0, new_t, None, half_ms=3)
    ev.btn_fit.setChecked(False)
    e, why = ev.session.add(0, pk, fit=False)
    assert why == "ok"
    out = tmp / "r2.csv"
    MR.export_review(str(out), ev.session, ev.review, "synthetic.abf")
    rows = list(csv.DictReader(open(out)))
    assert any(r["source"] == "manual_add" for r in rows)
    ev._undo(True)
    MR.export_review(str(out), ev.session, ev.review, "synthetic.abf")
    assert not any(r["source"] == "manual_add" for r in csv.DictReader(open(out)))


def test_borderline_right_after_an_accepted_event_is_not_offered():
    ts = 0.05                                                 # ms per sample (20 kHz)
    ms = lambda x: int(round(x / ts))
    cands = [MR.Cand(0, ms(100), 0.95, True),
             MR.Cand(0, ms(108), 0.55, False),                # 8 ms after an accepted event: shadowed
             MR.Cand(0, ms(130), 0.55, False),                # 30 ms after: a genuine borderline
             MR.Cand(0, ms(95), 0.55, False),                 # 5 ms BEFORE: not shadowed (only 'after' is filtered)
             MR.Cand(1, ms(108), 0.55, False)]                # other record: no accepted event there
    st = MR.ReviewState(cands, 0.5, ts_ms=ts)                 # default window 10 ms
    assert st.is_shadowed(0, ms(108)) and not st.is_shadowed(0, ms(130)) and not st.is_shadowed(0, ms(95))
    assert not st.is_shadowed(1, ms(108))
    queued = {(c.record, c.peak_idx) for c in st.queue}
    assert (0, ms(108)) not in queued and (0, ms(130)) in queued
    assert {c.peak_idx for c in st.candidates_in(0, False)} == {ms(130), ms(95)}
    st.set_shadow(0)                                          # 0 = off
    assert (0, ms(108)) in {(c.record, c.peak_idx) for c in st.queue}
    st.set_shadow(5)
    assert not st.is_shadowed(0, ms(108))                     # 8 ms is outside a 5 ms window
    assert st.cands[1].accepted is False                      # filtering never changes the candidate list / export


def test_gui_shadow_setting_is_applied_on_load(setup):
    w, ev, path, peaks, false_t, tmp = setup
    ev.sp_shadow.setValue(10.0)
    assert ev.load_candidates_file(path)
    n10 = len(ev.review.queue)
    ev.sp_shadow.setValue(0.0)
    assert len(ev.review.queue) >= n10 and ev.review.shadow_ms == 0.0
