"""Classifier inference plumbing with a randomly initialised bundle (no trained weights in the repository)."""
import os

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("sklearn")
import torch
from sklearn.ensemble import RandomForestClassifier

from ephys_suite.io import Recording
from ephys_suite.ml import core as C
from ephys_suite.ml import cnn as N
from ephys_suite.ml import infer
from ephys_suite import model_review as MR
from test_synthetic import synth_events


def _bundle(threshold=0.0):
    torch.manual_seed(0)
    cfg = dict(width=8, drop=0.1)
    nets = [N.Net(len(C.FEAT_NAMES), cfg["width"], cfg["drop"], cin=4).state_dict()]
    rng = np.random.default_rng(0)
    Fm = rng.normal(size=(200, len(C.FEAT_NAMES))).astype(np.float32)
    rf = RandomForestClassifier(n_estimators=5, random_state=0).fit(Fm, rng.integers(0, 2, 200))
    return dict(rf=rf, cnn_cfg=cfg, prep_kw=dict(pre=100, post=400, ms=True), cnn_states=nets, cin=4,
                mu=np.zeros(len(C.FEAT_NAMES), np.float32), sd=np.ones(len(C.FEAT_NAMES), np.float32), threshold=threshold)


@pytest.fixture
def rec():
    t, y, times, amp = synth_events(noise=2.0, n=12, dur_s=5)
    return Recording("synthetic.abf", t, [y[None, :]], ["pA"], ["IN 0"], np.array([0.0]), None, "", 20000.0), times


def test_wide_window_shapes_and_prep():
    y = np.random.default_rng(0).normal(size=20000)
    assert C.window_wide(y, 5000).shape == (C.WPRE + C.WPOST,)
    assert C.window_wide(y, 100) is None
    X = np.random.default_rng(1).normal(size=(4, C.WPRE + C.WPOST)).astype(np.float32)
    Fe = np.random.default_rng(2).normal(size=(4, len(C.FEAT_NAMES))).astype(np.float32)
    assert N.prep(X, Fe, pre=100, post=400).shape == (4, 2, 500)
    assert N.prep(X, Fe, pre=100, post=400, ms=True).shape == (4, 4, 500)


def test_classify_writes_reviewable_candidates(rec, tmp_path):
    r, times = rec
    rows = infer.classify_recording(r, _bundle(threshold=0.0))
    assert rows and set(rows[0]) == set(infer.COLUMNS)
    assert all(x["accepted"] == 1 for x in rows)                   # threshold 0 accepts everything kept
    for t in times:                                                 # the detectors propose every planted event
        assert any(abs(x["time_in_sweep_ms"] - (t + 1.5)) < 6 for x in rows), t
    hi = infer.classify_recording(r, _bundle(threshold=2.0), threshold=2.0)
    assert all(x["accepted"] == 0 for x in hi)
    out = tmp_path / "c.csv"
    infer.write_candidates(rows, str(out))
    assert os.path.getsize(out) > 0


def test_duplicate_suppression():
    sweep = np.array([0, 0, 0, 1]); idx = np.array([1000, 1020, 2000, 1010]); sc = np.array([0.6, 0.9, 0.7, 0.8])
    out = infer.suppress_duplicates(sweep, idx, sc)
    assert out.tolist() == [0.0, 0.9, 0.7, 0.8]


def test_shape_flag_separates_epsc_from_large_and_slow_events():
    n = C.WPRE + C.WPOST
    tt = np.arange(n) / C.FS * 1000.0
    def ev(depth, tau):
        y = np.zeros(n); k = C.WPRE; m = tt >= tt[k]
        y[m] = -depth * (np.exp(-(tt[m] - tt[k]) / tau) - np.exp(-(tt[m] - tt[k]) / 0.4)) / 0.9
        return y
    assert not C.is_large_or_slow(*C.shape_features(ev(20, 4.0)))        # typical sEPSC
    assert C.is_large_or_slow(*C.shape_features(ev(200, 4.0)))           # very large
    assert C.is_large_or_slow(*C.shape_features(ev(50, 80.0)))           # slow step / escape current
    assert not C.is_large_or_slow(*C.shape_features(ev(5, 80.0)))        # tiny + slow recovery is noise-limited, not flagged


def test_flagged_events_are_not_accepted_by_default(rec):
    r, _ = rec
    rows = infer.classify_recording(r, _bundle(threshold=0.0))
    assert set(x["flag"] for x in rows) <= {"", "large_slow", "near_large"}
    assert all(not (x["flag"] and x["accepted"]) for x in rows)
    assert "threshold" in infer.LAST
