import os

import numpy as np
import pytest

from ephys_suite import events as E
from ephys_suite import model_review as MR
from ephys_suite import verify as V
from test_synthetic import synth_events

FS = 20000.0


def build(noise=2.0, n_extra_weak=1, seed=5):
    """A 2-record trace. Returns session (events = the 'model-accepted' ones), review state, truth dict."""
    t, y, times, amp = synth_events(noise=noise, n=25, dur_s=6, seed=seed, amps=(-80, -30))
    Y = np.vstack([y, y.copy()])
    # a weak but real event in record 1 that the 'model' misses
    at = 2500.0
    x = t - at
    m = x >= 0
    Y[1][m] += -14 * (1 - np.exp(-x[m] / 0.6)) * np.exp(-x[m] / 6) / 0.55
    s = E.EventSession(t, Y, E.EventParams(), np.array([0.0, 6000.0]))
    s.detect()
    peaks_strong = [(0, e.peak_idx) for e in s.events if e.record == 0]
    s.load_external(peaks_strong)                       # model accepted record-0 events only
    cands = [MR.Cand(r, k, 0.95 - 0.02 * i, True) for i, (r, k) in enumerate(peaks_strong)]
    # two borderline (not accepted) candidates in record 0: real-looking positions of record-1 events at records 1
    rec1 = [e for e in E.EventSession(t, Y, E.EventParams(), np.array([0.0, 6000.0])).detect().events if e.record == 1][:2]
    cands += [MR.Cand(1, rec1[0].peak_idx, 0.45, False), MR.Cand(1, rec1[1].peak_idx, 0.30, False)]
    review = MR.ReviewState(cands, 0.5, "synthetic")
    return s, review, dict(t=t, weak_peak=int(round((at + 1.5) / (1000 / FS))))


# -------------------------------------------------------------------------------------------------- coverage
def test_coverage_merge_fraction_and_gaps():
    c = V.Coverage([1000.0, 500.0])
    c.add(0, 100, 300)
    c.add(0, 250, 400)                      # overlaps -> merged
    c.add(0, 400, 450)                      # touches -> merged
    assert c.iv[0] == [[100, 450]]
    c.add(0, -50, 20)                       # clipped to the record
    assert c.iv[0][0] == [0, 20]
    assert c.fraction(0) == pytest.approx((20 + 350) / 1000)
    assert c.fraction() == pytest.approx((20 + 350) / 1500)
    assert c.gaps(0) == [(20, 100), (450, 1000)]
    assert c.gaps(1) == [(0, 500)]


def test_next_gap_goes_forward_wraps_and_ends():
    c = V.Coverage([1000.0, 1000.0])
    c.add(0, 0, 600)
    assert c.next_gap(0, 0) == (0, 600, 1000)
    assert c.next_gap(0, 700) == (0, 700, 1000)           # starts at the current position inside the gap
    c.add(0, 600, 1000)
    assert c.next_gap(0, 0) == (1, 0, 1000)               # moves on to the next record
    c.add(1, 0, 1000)
    assert c.next_gap(0, 0) is None and c.fraction() == pytest.approx(1.0)
    c2 = V.Coverage([1000.0, 1000.0])
    c2.add(1, 0, 1000)
    c2.add(0, 500, 1000)
    assert c2.next_gap(1, 0) == (0, 0, 500)               # wraps back to the start of the trace


def test_coverage_json_roundtrip():
    c = V.Coverage([1000.0, 500.0])
    c.add(0, 10, 200)
    c.add(1, 0, 500)
    d = V.Coverage([1000.0, 500.0])
    d.load_json(c.to_json())
    assert d.iv == c.iv


# ------------------------------------------------------------------------------------------ decisions / undo
def test_decisions_share_one_undo_history():
    s, review, _ = build()
    e = s.events[0]
    assert not s.is_verified(e)
    s.confirm(e)
    assert s.is_verified(e) and s.decision(e.record, e.peak_idx) == "confirmed"
    n = len(s.events)
    e2 = s.events[1]
    s.reject(e2)
    assert len(s.events) == n - 1 and s.decision(e2.record, e2.peak_idx) == "rejected" and (e2.record, e2.peak_idx) in s.log.rejected
    assert s.undo()                                       # ONE undo restores event and removes the 'rejected' decision
    assert len(s.events) == n and s.decision(e2.record, e2.peak_idx) is None
    assert s.undo() and not s.is_verified(e)              # next undo reverts the confirmation


def test_unreject_brings_the_event_back_and_add_with_confirm():
    s, review, truth = build()
    e = s.events[2]
    k = e.peak_idx
    s.reject(e)
    assert s.find_event(0, k) is None
    back = s.unreject(0, k)
    assert back is not None and s.decision(0, k) is None
    # adding a missed event with confirm=True records the decision in the same step
    ev, why = s.add(1, truth["weak_peak"], fit=False, confirm=True)
    assert why == "ok" and s.is_verified(ev) and s.decision(1, truth["weak_peak"]) == "confirmed"
    n = len(s.events)
    s.undo()
    assert len(s.events) == n - 1 and s.decision(1, truth["weak_peak"]) is None


def test_hand_added_events_count_as_verified():
    s, review, truth = build()
    ev, _ = s.add(1, truth["weak_peak"], fit=False)
    assert s.is_verified(ev)


# ------------------------------------------------------------------------------------------------- queues
def test_queue_orders_and_excludes_done_items():
    s, review, _ = build()
    q = V.build_queue("lowest_score", s, review)
    sc = [x.score for x in q]
    assert sc == sorted(sc) and len(q) == len(s.events)
    s.confirm(s.find_event(q[0].record, q[0].peak_idx))
    assert len(V.build_queue("lowest_score", s, review)) == len(q) - 1
    assert len(V.build_queue("lowest_score", s, review, include_done=True)) == len(q)
    qt = V.build_queue("time", s, review)
    assert [x.peak_idx for x in qt] == sorted(x.peak_idx for x in qt)


def test_borderline_queue_is_closest_to_threshold_first_and_dismiss_removes():
    s, review, _ = build()
    q = V.build_queue("borderline", s, review)
    assert [round(x.score, 2) for x in q] == [0.45, 0.30]
    s.dismiss(q[0].record, q[0].peak_idx)
    assert [round(x.score, 2) for x in V.build_queue("borderline", s, review)] == [0.30]
    s.undo()
    assert len(V.build_queue("borderline", s, review)) == 2


# ---------------------------------------------------------------------------------------------- suspects
def test_second_opinion_finds_the_weak_event_the_model_missed():
    s, review, truth = build()
    tol = int(4.0 / s.ts)
    skip = lambda r, k: s.find_event(r, k, tol) is not None
    sus = V.find_suspects(s, skip, sd=2.5, min_snr=3.0)
    assert sus and all(not skip(x.record, x.peak_idx) for x in sus)
    assert [x.snr for x in sus] == sorted((x.snr for x in sus), reverse=True)
    near = [x for x in sus if x.record == 1 and abs(x.peak_idx - truth["weak_peak"]) < int(5.0 / s.ts)]
    assert near, "the weak real event should be proposed"
    # it becomes an event, then it is no longer a suspect
    ev, why = s.add(1, near[0].peak_idx, fit=False, confirm=True)
    assert why == "ok"
    assert not any(q.record == 1 and abs(q.peak_idx - truth["weak_peak"]) < int(5.0 / s.ts) for q in V.build_queue("suspects", s, review, sus))


def test_suspects_respect_dismissals_and_pure_noise_gives_few():
    s, review, truth = build()
    sus = V.find_suspects(s, lambda r, k: s.find_event(r, k, int(4 / s.ts)) is not None)
    first = sus[0]
    s.dismiss(first.record, first.peak_idx)
    assert all((q.record, q.peak_idx) != (first.record, first.peak_idx) for q in V.build_queue("suspects", s, review, sus))
    t, y, *_ = synth_events(noise=3.0, n=0 + 1, dur_s=4, seed=9, amps=(-1, -1))      # (almost) pure noise
    s2 = E.EventSession(t, y[None, :], E.EventParams())
    s2.detect()
    few = V.find_suspects(s2, lambda r, k: False, sd=3.5, min_snr=4.5)
    assert len(few) <= 3


# -------------------------------------------------------------------------------------------- stats / ready
def test_stats_and_record_ready_to_mark():
    s, review, truth = build()
    sus = []
    st = V.stats(s, review, sus, V.Coverage([6000.0, 6000.0]))
    assert st["events"] == len(s.events) and st["unverified"] == len(s.events) and st["confirmed"] == 0 and st["coverage"] == 0
    assert st["borderline_open"] == 2
    cov = V.Coverage([6000.0, 6000.0])
    cov.add(0, 0, 6000)
    assert not V.record_ready_to_mark(s, 0, cov, review, sus)           # events still unverified
    for e in list(s.events):
        s.confirm(e)
    assert V.record_ready_to_mark(s, 0, cov, review, sus)
    assert not V.record_ready_to_mark(s, 1, cov, review, sus)           # record 1 was not viewed, has open candidates
    st = V.stats(s, review, sus, cov)
    assert st["unverified"] == 0 and st["confirmed"] == len(s.events) and st["coverage"] == pytest.approx(0.5)


# ------------------------------------------------------------------------------------------ zoom card helpers
def test_snippet_ghost_and_local_baseline():
    s, review, _ = build()
    e = s.events[3]
    tr, y, i0 = V.snippet(s, e.record, e.peak_idx)
    assert tr[i0] == 0 and y[i0] == pytest.approx(e.peak_im)
    g = V.ghost_template(s)
    assert g is not None and g[1][int(np.argmin(np.abs(g[0])))] == pytest.approx(1.0, abs=0.35)
    assert abs(V.local_baseline(s, e.record, e.peak_idx) - e.bl_im) < 8


# ---------------------------------------------------------------------------------------------- progress
def test_progress_roundtrip_restores_everything(tmp_path):
    s, review, truth = build()
    cov = V.Coverage([6000.0, 6000.0])
    cov.add(0, 0, 3000)
    s.confirm(s.events[0])
    s.reject(s.events[1])
    ev, _ = s.add(1, truth["weak_peak"], fit=False, baseline=(truth["weak_peak"] - 100, -1.0), confirm=True)
    s.dismiss(1, 12345)
    review.reviewed = {0}
    review.visited = {(0, 5)}
    path = V.progress_path("rec.abf", str(tmp_path / "cands.csv"))
    assert path.endswith("cands.review_progress.json")
    V.save_progress(path, s, review, cov, "rec.abf", "cands.csv", "time", 7)
    d = V.load_progress(path)
    assert d["mode"] == "time" and d["pos"] == 7 and "confirmed" in V.progress_summary(d)
    # fresh session = what loading the candidates again gives, then resume
    s2, review2, _ = build()
    cov2 = V.Coverage([6000.0, 6000.0])
    assert V.apply_progress(d, s2, review2, cov2)
    assert s2.log.confirmed == s.log.confirmed and s2.log.rejected == s.log.rejected and s2.log.dismissed == s.log.dismissed
    assert [(e.record, e.peak_idx) for e in s2.events] == [(e.record, e.peak_idx) for e in s.events]
    e2 = s2.find_event(1, truth["weak_peak"])
    assert e2.baseline_manual and e2.bl_im == pytest.approx(-1.0)
    assert cov2.iv == cov.iv and review2.reviewed == {0} and review2.visited == {(0, 5)}


def test_progress_rejects_a_different_recording_and_bad_files(tmp_path):
    s, review, _ = build()
    p = V.save_progress(str(tmp_path / "p.json"), s, review, None, "rec.abf")
    d = V.load_progress(p)
    d["n_samples"] += 1
    assert not V.apply_progress(d, s, review, None)
    assert V.load_progress(str(tmp_path / "missing.json")) is None
    (tmp_path / "bad.json").write_text("{not json")
    assert V.load_progress(str(tmp_path / "bad.json")) is None
    (tmp_path / "old.json").write_text('{"version": 0}')
    assert V.load_progress(str(tmp_path / "old.json")) is None


def test_ghost_falls_back_to_all_events_until_three_are_verified():
    s, review, _ = build()
    assert V.ghost_template(s) is not None                      # nothing verified yet
    s.confirm(s.events[0])
    s.confirm(s.events[1])
    assert V.ghost_template(s) is not None                      # two verified: still uses all events (regression)
    for e in s.events[2:6]:
        s.confirm(e)
    g = V.ghost_template(s)
    assert g is not None and g[1].max() == pytest.approx(1.0, abs=0.3)


def test_noise_estimate_sees_slow_fluctuations_that_first_differences_miss():
    rng = np.random.default_rng(3)
    n = 200000
    white = rng.normal(0, 1.0, n)
    # slow (~20 ms) coloured fluctuations of SD ~3 plus a little white noise: what real recordings look like
    k = np.ones(400) / 400
    coloured = np.convolve(rng.normal(0, 1, n + 399), k, mode="valid") * 50 + rng.normal(0, 0.8, n)
    assert V.robust_noise(white, 0.05) == pytest.approx(1.0, rel=0.05)
    diff_based = 1.4826 * np.median(np.abs(np.diff(coloured) - np.median(np.diff(coloured)))) / np.sqrt(2)
    assert V.robust_noise(coloured, 0.05) > 2.0 * diff_based
    drifting = white + np.linspace(0, 200, n)                      # slow drift must not count as noise
    assert V.robust_noise(drifting, 0.05) == pytest.approx(1.0, rel=0.1)
    withev = white.copy()
    withev[1000:1200] -= 80                                          # events do not inflate it
    assert V.robust_noise(withev, 0.05) == pytest.approx(1.0, rel=0.05)
