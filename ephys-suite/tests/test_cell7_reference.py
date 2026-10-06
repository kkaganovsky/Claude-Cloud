"""Reproduce the user's reference spreadsheet row for the example cell (cell 7, DLX kglu next to DLX cells FS?).

Exact matches are asserted for everything recoverable from the data; the passive values depend on region choices made
in Easy Electrophysiology that cannot be recovered, so only close agreement is asserted (see README: DECISIONS)."""
import pytest

from ephys_suite.io import load_abf
from ephys_suite.report import build_cell_report, make_config

EXACT = {"Max AP": 80, "Adaptation": 0.59235669, "Rheobase (pA)": 401.549931, "Amplitude (mV)": 78.6865234,
         "Threshold (mV)": -47.503662, "Corr Threshold": -57.183662, "Rise Time (ms)": 0.325, "Decay Time (ms)": 0.855,
         "Half-Width (ms)": 0.73, "fAHP (mV)": -21.557617, "mAHP (mV)": -17.205811}
CLOSE = {"steady state change in Voltage (sag)": (-14.633664, 0.1), "Input Resistance": (28.3091705, 0.5),
         "Sag mV": (-1.3969184, 0.02), "Sag Ratio": (0.08714084, 0.002), "Tolias ratio": (1.09545924, 0.002),
         "tau (ms)": (21.2586734, 0.3), "Cm (pF)": (750.946529, 20.0), "RMP original": (-73.56597086, 0.01),
         "RMP corrected": (-83.24597086, 0.01)}


@pytest.fixture(scope="module")
def rep(cell7_dir):
    # The reference row's Rin was made in Easy Electrophysiology from the hyperpolarising steps only, so the 0 pA sweep
    # (on by default in the suite) is switched off to reproduce it. With it on: Rin 29.45 MOhm, Cm 718 pF.
    cfg = make_config(load_abf(cell7_dir + "IO.abf"))
    cfg.passive.include_zero = False
    return build_cell_report(cell7_dir + "IO.abf", cfg, spont_path=cell7_dir + "spontanous?.abf")


@pytest.mark.parametrize("col,val", list(EXACT.items()))
def test_exact(rep, col, val):
    assert float(rep.row[col]) == pytest.approx(val, abs=1e-3)


@pytest.mark.parametrize("col,spec", list(CLOSE.items()))
def test_close(rep, col, spec):
    val, tol = spec
    assert abs(float(rep.row[col]) - val) <= tol


def test_fs_latency_is_measured_from_the_actual_step_start(rep):
    # the reference 593.1 ms equals 682.2 (first AP peak) - 89.1 (Im start of the 2.5 s protocol), not IO.abf's 578.15
    assert float(rep.row["FS Latency (ms)"]) == pytest.approx(104.05, abs=0.01)
    assert 682.2 - 89.1 == pytest.approx(593.1)


def test_decisions_are_logged(rep):
    keys = [k for k, _ in rep.decisions]
    for k in ("Passive windows", "Rin method", "AP detection", "Rheobase", "Adaptation", "AP kinetics", "RMP", "Cm"):
        assert k in keys


def test_sEPSC_example_runs(cell7_dir):
    from ephys_suite import events as E
    from ephys_suite.io import load_abf
    r = load_abf(cell7_dir + "sEPSC.abf")
    p = E.EventParams(omit=((0.0, 400.0),))      # defaults; omit the test pulse at the start of every sweep
    res = E.analyse_events(r.t, r.ch[0], p, r.sweep_start_ms)
    s = E.summarize(res)
    assert 30 <= s["n"] <= 300 and s["amplitude_median"] < -10      # RMS lower threshold (2 x RMS) is the default
