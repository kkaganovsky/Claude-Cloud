"""The verify workflow in the Events tab (offscreen): queues, decisions, undo, coverage, overview strip, gallery, resume, export."""
import csv
import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
try:
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from ephys_suite.gui import MainWindow
except Exception as e:                                       # pragma: no cover
    pytest.skip(f"Qt unavailable: {e}", allow_module_level=True)

from ephys_suite import model_review as MR
from ephys_suite import verify as V
from ephys_suite.io import Recording
from test_synthetic import synth_events

TS = 0.05


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_window(tmp_path, app):
    t, y, times, amp = synth_events(noise=3.0, n=40, dur_s=8, seed=11, amps=(-90, -25))
    Y = np.vstack([y, y.copy()])
    x = t - 2500.0
    m = x >= 0
    Y[1][m] += -16 * (1 - np.exp(-x[m] / 0.6)) * np.exp(-x[m] / 6) / 0.55          # weak real event nobody proposed
    rec = Recording(str(tmp_path / "synthetic.abf"), t, [Y], ["pA"], ["IN 0"], np.array([0.0, 8000.0]), None, "", 20000.0)
    w = MainWindow()
    w.rec = rec
    w.events.settings.clear()
    w.events.set_recording(rec)
    w.show()
    return w, t, times


def write_cands(tmp_path, times):
    rows = []
    for i, tm in enumerate(times):
        rows.append((1, tm + 1.5, 0.55 + 0.4 * (i % 5) / 5, 1))                       # increasing confidence
    rows.append((1, times[3] + 60, 0.72, 1))                                          # accepted false positive (noise)
    rows.append((1, times[5] + 90, 0.42, 0))                                          # borderline, noise
    rows.append((1, times[7] + 1.5 + 200, 0.40, 0))                                   # borderline, noise
    for i, tm in enumerate(times[:30]):
        rows.append((2, tm + 1.5, 0.9, 1))
    p = tmp_path / "cands.csv"
    with open(p, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["sweep", "time_in_sweep_ms", "score", "accepted"])
        wr.writerows(rows)
    return str(p)


@pytest.fixture
def env(app, tmp_path):
    w, t, times = make_window(tmp_path, app)
    path = write_cands(tmp_path, times)
    ev = w.events
    ev.vw.setValue(1500)
    ev.load_candidates_file(path)
    app.processEvents()
    yield w, ev, ev.kit, path, t, times, tmp_path
    w.close()


# ------------------------------------------------------------------------------------------ verifying events
def test_loading_candidates_starts_at_the_least_sure_event(env):
    w, ev, kit, *_ = env
    assert kit.mode == "lowest_score" and kit.current() is not None
    assert kit.current().score == min(q.score for q in kit.queue)
    assert kit.b_yes.isEnabled() and "Confirm" in kit.b_yes.text()
    assert "to verify" in kit.lbl_stats.text()
    s = ev.session
    assert not s.is_verified(s.find_event(kit.current().record, kit.current().peak_idx, 80))


def test_confirm_advances_and_counts(env):
    w, ev, kit, *_ = env
    n_open = len(kit.queue)
    first = kit.current().key
    kit.confirm_current()
    assert len(kit.queue) == n_open - 1 and kit.current().key != first
    assert ev.session.decision(*first) == "confirmed"
    st = V.stats(ev.session, ev.review, kit.suspects, kit.cov)
    assert st["confirmed"] == 1 and st["unverified"] == n_open - 1


def test_reject_removes_event_records_decision_and_undo_jumps_back(env):
    w, ev, kit, *_ = env
    s = ev.session
    key = kit.current().key
    n = len(s.events)
    kit.reject_current()
    assert len(s.events) == n - 1 and key in s.log.rejected and s.find_event(*key, 5) is None
    nxt = kit.current().key
    ev._undo(True)                                           # the tab's ordinary Ctrl+Z
    assert len(s.events) == n and key not in s.log.rejected
    assert kit.current().key == key and nxt != key           # ... and the card is back on the item that was un-decided


def test_delete_key_on_a_selected_event_is_recorded_as_a_rejection(env):
    w, ev, kit, *_ = env
    e = ev.session.events[4]
    ev.select(e)
    ev.delete_selected()
    assert (e.record, e.peak_idx) in ev.session.log.rejected


def test_borderline_queue_accept_and_dismiss(env):
    w, ev, kit, *_ = env
    kit.cb_mode.setCurrentIndex(kit.cb_mode.findData("borderline"))
    assert kit.mode == "borderline" and len(kit.queue) >= 2
    assert kit.b_yes.text().startswith("✓ Accept")
    cur = kit.current()
    assert cur.kind == "candidate"
    n = len(ev.session.events)
    kit.confirm_current()                                    # accept the closest-to-threshold candidate
    assert len(ev.session.events) == n + 1 and ev.session.find_event(cur.record, cur.peak_idx, 40) is not None
    nxt = kit.current()
    kit.reject_current()                                     # dismiss the next one
    assert ev.session.decision(nxt.record, nxt.peak_idx) == "dismissed"
    assert all(q.key != nxt.key for q in kit.queue)


# --------------------------------------------------------------------------------- finding missed events
def test_second_opinion_flags_the_weak_event_and_y_adds_it(env):
    w, ev, kit, *_ = env
    kit.next_suspect(+1)
    assert kit.mode == "suspects" and kit.suspects is not None and kit.queue
    weak = int(round(2501.5 / TS))
    near = [q for q in kit.queue if q.record == 1 and abs(q.peak_idx - weak) < 100]
    assert near, "the weak real event should be among the possible misses"
    kit.pos = kit.queue.index(near[0])
    kit.focus(kit.current())
    assert "SNR" in kit.lbl_info.text() and kit.b_yes.text().startswith("✓ Add")
    n = len(ev.session.events)
    kit.confirm_current()
    e = ev.session.find_event(1, near[0].peak_idx, 40)
    assert len(ev.session.events) == n + 1 and e is not None and e.fitted and ev.session.is_verified(e)
    assert all(abs(q.peak_idx - near[0].peak_idx) > 40 or q.record != 1 for q in kit.queue)


def test_clicking_a_suspect_focuses_then_adds(env):
    w, ev, kit, *_ = env
    kit.ensure_suspects()
    q = kit.suspects[0]
    ev.spin.setValue(q.record)
    ev.vw.setValue(1500)
    t = w.rec.t
    ev._set_view(max(0.0, t[q.peak_idx] - 750), 1500)
    y = ev.session.Y[q.record][q.peak_idx]
    n = len(ev.session.events)
    assert ev.on_click(t[q.peak_idx], y, ev.vb) and len(ev.session.events) == n and kit.mode == "suspects"
    assert kit.current().key == (q.record, q.peak_idx)
    assert ev.on_click(t[q.peak_idx], y, ev.vb)
    assert len(ev.session.events) == n + 1


def test_dismissed_suspects_stay_dismissed(env):
    w, ev, kit, *_ = env
    kit.next_suspect(+1)
    key = kit.current().key
    kit.reject_current()
    assert ev.session.decision(*key) == "dismissed" and all(q.key != key for q in kit.queue)


# ------------------------------------------------------------------------------------------- coverage
def test_coverage_counts_only_narrow_windows_after_dwell(env):
    w, ev, kit, *_ = env
    assert kit.cov.fraction() == 0
    ev._set_view(0.0, 7000.0)                                # whole record: too wide to see events
    kit._count_view()
    assert kit.cov.fraction() == 0
    ev._set_view(1000.0, 1500.0)
    kit._count_view()
    assert kit.cov.iv[0] == [[1000.0, 2500.0]] and kit.cov.fraction() == pytest.approx(1500 / 16000, abs=1e-3)
    assert "Trace viewed" in kit.lbl_cov.text()


def test_next_unviewed_walks_the_whole_trace_and_finishes(env):
    w, ev, kit, *_ = env
    seen = 0
    for _ in range(40):
        kit._count_view()
        kit.next_unviewed()
        seen += 1
        if kit.cov.fraction() >= 0.995:
            break
        (x0, x1), _ = ev.vb.viewRange()
    kit._count_view()
    for _ in range(40):
        if kit.cov.fraction() >= 0.995:
            break
        kit.next_unviewed()
        kit._count_view()
    assert kit.cov.fraction() >= 0.995
    assert kit.cov.next_gap(0, 0) is None
    kit.next_unviewed()
    assert "whole trace has been viewed" in ev.hint.text()


def test_record_ready_prompt_when_all_viewed_and_verified(env):
    w, ev, kit, *_ = env
    kit.cov.add(1, 0, 8000)
    for q in list(V.build_queue("lowest_score", ev.session, ev.review)):
        e = ev.session.find_event(q.record, q.peak_idx, 40)
        if e is not None and q.record == 1:
            ev.session.confirm(e)
    for q in V.build_queue("borderline", ev.session, ev.review):
        if q.record == 1:
            ev.session.dismiss(q.record, q.peak_idx)
    kit.ensure_suspects()
    for q in V.build_queue("suspects", ev.session, ev.review, kit.suspects):
        if q.record == 1:
            ev.session.dismiss(q.record, q.peak_idx)
    ev.spin.setValue(1)
    ev._refresh_all()
    assert "fully reviewed" in kit.lbl_stats.text()


# ------------------------------------------------------------------------------------------- overview strip
def test_minimap_geometry_click_and_drag_jump(env):
    w, ev, kit, *_ = env
    mm = kit.mm
    assert len(mm.offsets) == 2 and mm.offsets[1] > mm.lengths[0]
    r, t = mm.from_x(mm.offsets[1] + 3000)
    assert (r, round(t)) == (1, 3000)
    a, b = mm.region.getRegion()
    mm.region.setRegion((mm.offsets[1] + 4000, mm.offsets[1] + 4000 + (b - a)))       # user drags the window
    assert ev.rec_i == 1
    (x0, x1), _ = ev.vb.viewRange()
    assert x0 == pytest.approx(4000, abs=1) and (x1 - x0) == pytest.approx(b - a, abs=1)
    ev._set_view(500.0, 1500.0)                                                        # and the strip follows the main view
    ra, rb = mm.region.getRegion()
    assert ra == pytest.approx(mm.offsets[1] + 500, abs=1) and rb - ra == pytest.approx(1500, abs=1)


# ---------------------------------------------------------------------------------------------- gallery
def test_gallery_flip_and_decide_rest(env):
    w, ev, kit, *_ = env
    kit.open_gallery()
    g = kit.gallery
    assert g.mode() == "lowest_score" and len(g.plots) == g.per_page() == 20
    it = g.items[0]
    s = ev.session
    assert g.state(it) == "event"
    g.glw.scene().sigMouseClicked.emit  # (scene click is exercised through flip below)
    kit.flip(it, g.mode())
    g.draw()
    assert g.state(it) == "rejected" and s.find_event(it.record, it.peak_idx, 40) is None
    kit.flip(it, g.mode())                                      # second click brings it back
    g.draw()
    assert g.state(it) in ("event", "confirmed") and s.find_event(it.record, it.peak_idx, 40) is not None
    g.decide_rest()                                             # confirm the page, turn to the next
    assert g.page == 1 and all(g.state(i) == "confirmed" for i in g.items[:20])
    assert V.stats(s, ev.review, None, None)["confirmed"] >= 20


def test_gallery_for_borderline_candidates_click_adds_and_rest_is_dismissed(env):
    w, ev, kit, *_ = env
    kit.open_gallery()
    g = kit.gallery
    g.open_with("borderline")
    assert g.items and "add" in g.hint.text()
    first, rest = g.items[0], g.items[1:]
    kit.flip(first, "borderline")
    g.decide_rest()
    s = ev.session
    assert s.find_event(first.record, first.peak_idx, 40) is not None
    assert all(s.decision(i.record, i.peak_idx) == "dismissed" for i in rest[:g.per_page() - 1])


# ---------------------------------------------------------------------------------- keyboard and focus
def test_keyboard_flow(env, app):
    w, ev, kit, *_ = env
    w.tabs.setCurrentIndex(2)
    w.activateWindow()
    ev.pd.setFocus()
    app.processEvents()

    def key(k, mod=Qt.NoModifier):
        QTest.keyClick(ev.pd, k, mod)
        app.processEvents()
    n0 = len(kit.queue)
    key(Qt.Key_Y)
    assert len(kit.queue) == n0 - 1
    n = len(ev.session.events)
    key(Qt.Key_N)
    assert len(ev.session.events) == n - 1
    k0 = kit.current().key
    key(Qt.Key_Period)
    assert kit.current().key != k0
    key(Qt.Key_Comma)
    assert kit.current().key == k0
    key(Qt.Key_F)
    assert kit.mode == "suspects"
    key(Qt.Key_G)
    assert kit.gallery is not None and kit.gallery.isVisible()
    kit.gallery.close()
    key(Qt.Key_Z, Qt.ControlModifier)                          # ordinary undo still works


def test_typing_in_a_box_does_not_trigger_shortcuts(env, app):
    w, ev, kit, *_ = env
    w.tabs.setCurrentIndex(2)
    ev.vx.setFocus()
    app.processEvents()
    n = len(kit.queue)
    QTest.keyClick(ev.vx, Qt.Key_Y)
    assert len(kit.queue) == n


# ------------------------------------------------------------------------------- persistence / export
def test_autosave_and_resume_in_a_new_session(env, app, monkeypatch):
    w, ev, kit, path, t, times, tmp = env
    kit.confirm_current()
    kit.confirm_current()
    kit.reject_current()
    pk = ev.session.snap_peak(0, times[2] + 700, None, half_ms=3)
    ev.session.add(0, pk, fit=False)
    ev._refresh_all()
    kit.cov.add(0, 0, 3000)
    kit.autosave()
    pp = V.progress_path(w.rec.path, path)
    d = json.load(open(pp))
    assert len(d["log"]["confirmed"]) == 2 and len(d["log"]["rejected"]) == 1 and d["coverage"]["0"] == [[0.0, 3000.0]]
    want_conf, want_rej = set(map(tuple, d["log"]["confirmed"])), set(map(tuple, d["log"]["rejected"]))
    # new window, same recording and candidates: the question is answered "yes"
    w2 = MainWindow()
    w2.rec = w.rec
    w2.events.settings.clear()
    w2.events.set_recording(w.rec)
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.Yes)
    w2.events.load_candidates_file(path)
    assert asked and "2 confirmed, 1 rejected" in asked[0]
    s2 = w2.events.session
    assert s2.log.confirmed == want_conf and s2.log.rejected == want_rej
    assert w2.events.kit.cov.iv[0] == [[0.0, 3000.0]]
    assert s2.find_event(0, pk, 40) is not None                  # the hand-added event survived
    assert len(w2.events.kit.queue) == len(kit.queue)
    w2.close()


def test_declining_to_resume_starts_fresh(env, app, monkeypatch):
    w, ev, kit, path, t, times, tmp = env
    kit.confirm_current()
    kit.autosave()
    w2 = MainWindow()
    w2.rec = w.rec
    w2.events.settings.clear()
    w2.events.set_recording(w.rec)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.No)
    w2.events.load_candidates_file(path)
    assert not w2.events.session.log.confirmed
    w2.events.kit.resume()                                      # ... but the button can still bring it back
    assert len(w2.events.session.log.confirmed) == 1
    w2.close()


def test_export_review_has_decisions_coverage_and_suspects(env):
    w, ev, kit, path, t, times, tmp = env
    kit.confirm_current()
    rej = kit.current().key
    kit.reject_current()
    kit.next_suspect(+1)
    sus = kit.current().key
    kit.confirm_current()
    kit.cov.add(0, 0, 4000)
    out = tmp / "review.csv"
    MR.export_review(str(out), ev.session, ev.review, "synthetic.abf", w.rec.path, coverage=kit.cov, suspects=kit.suspects)
    rows = list(csv.DictReader(open(out)))
    assert "decision" in rows[0] and "record_viewed_pct" in rows[0]
    by = {(int(r["sweep"]) - 1, int(r["peak_idx"])): r for r in rows}
    assert by[rej]["decision"] == "rejected" and by[rej]["final_event"] == "0"
    assert any(r["decision"] == "confirmed" for r in rows)
    assert rows[0]["record_viewed_pct"] != "" and any(r["source"] == "suspect" and r["final_event"] == "1" for r in rows)
    meta = json.load(open(tmp / "review.json"))
    assert meta["viewed_fraction_by_record"]["1"] == pytest.approx(0.5) and meta["viewed_intervals_ms"]["1"] == [[0.0, 4000.0]]
    # columns that existed before are still first, in the same order (older readers keep working)
    assert list(rows[0])[:10] == ["recording", "sweep", "time_in_sweep_ms", "peak_idx", "model_score", "model_accepted", "final_event",
                                  "source", "sweep_marked_reviewed", "visited"]


def test_verifying_without_a_model_uses_the_time_queue(app, tmp_path):
    w, t, times = make_window(tmp_path, app)
    ev = w.events
    ev.run()
    kit = ev.kit
    assert kit.mode == "time" and kit.queue and kit.current() is not None
    idx = [q.peak_idx for q in kit.queue if q.record == 0]
    assert idx == sorted(idx)
    n = len(kit.queue)
    kit.confirm_current()
    assert len(kit.queue) == n - 1
    w.close()


def test_candidate_without_a_findable_baseline_prompts_for_one(env):
    w, ev, kit, *_ = env
    kit.cb_mode.setCurrentIndex(kit.cb_mode.findData("borderline"))
    ev.p.baseline_search_ms = 0.01                                # nothing to search: automatic baseline impossible
    n = len(ev.session.events)
    kit.confirm_current()
    assert ev.pending is not None and ev.btn_bl.isChecked() and len(ev.session.events) == n
    assert "baseline" in ev.hint.text().lower()
    ev.cancel()
