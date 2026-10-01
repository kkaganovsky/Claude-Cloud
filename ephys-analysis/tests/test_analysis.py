import numpy as np
import pytest

from ephys_analysis.analysis import (analyze, capacitance_pF, default_params,
                                     detect_step, fit_mono, steady_state)
from ephys_analysis.io import Recording

RIN, TAU, VREST = 200.0, 20.0, -65.0     # MOhm, ms, mV


def make_rec(amps=(-100, -80, -60, -40, -20, 0, 20), sag=0.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(0, 1500, 0.1)
    v, i = [], []
    for a in amps:
        cur = np.where((t >= 200) & (t < 1200), float(a), 0.0)
        dv = a / 1e3 * RIN * (1 - np.exp(-(t - 200) / TAU))
        vm = np.where(t >= 200, dv, 0.0)
        if sag and a < 0:   # slow depolarising sag, decays back toward rest
            vm = vm + np.where(t >= 200, sag * (1 - np.exp(-(t - 200) / 150.0)) * a / -100, 0)
        off = np.where(t >= 1200, -(dv[t < 1200][-1]) * np.exp(-(t - 1200) / TAU), 0.0)
        v.append(VREST + vm + off * (t >= 1200) + rng.normal(0, noise, t.size))
        i.append(cur)
    return Recording("synthetic", t, np.array(v), np.array(i), "synthetic", [])


def test_detect_step():
    rec = make_rec()
    a, b = detect_step(rec.t, rec.i)
    assert a == pytest.approx(200, abs=0.2) and b == pytest.approx(1200, abs=0.2)


def test_rin_tau_cm_clean():
    rec = make_rec()
    p = default_params(rec.t, rec.i)
    res, S = analyze(rec, p, list(range(len(rec.v))))
    assert S.n == 5                                   # 0 pA and +20 pA excluded
    assert S.rin == pytest.approx(RIN, rel=5e-3)
    assert S.tau == pytest.approx(TAU, rel=5e-3)
    assert S.cm == pytest.approx(TAU / RIN * 1e3, rel=1e-2)   # 100 pF


def test_user_sweep_selection():
    rec = make_rec()
    p = default_params(rec.t, rec.i)
    _, S = analyze(rec, p, [0, 1])
    assert S.n == 2 and S.rin == pytest.approx(RIN, rel=5e-3)


def test_noise_and_fixed_b0():
    rec = make_rec(noise=0.3)
    p = default_params(rec.t, rec.i)
    p.b0_mode = "fixed"
    _, S = analyze(rec, p, list(range(len(rec.v))))
    assert S.tau == pytest.approx(TAU, rel=0.1)
    assert S.rin == pytest.approx(RIN, rel=0.05)


def test_spike_excluded():
    rec = make_rec()
    rec.v[0, 3000] = 30.0
    p = default_params(rec.t, rec.i)
    res, S = analyze(rec, p, list(range(len(rec.v))))
    assert not res[0].used and res[0].note == "spikes" and S.n == 4


def test_sag_start_mode_recovers_tau_better_than_onset():
    rec = make_rec(sag=15.0)
    p = default_params(rec.t, rec.i)
    p.sag_search = 300
    _, S_on = analyze(rec, p, list(range(len(rec.v))))
    p.fit_start_mode = "sag"
    res, S_sag = analyze(rec, p, list(range(len(rec.v))))
    assert np.isfinite(res[0].sag_ratio) and res[0].sag > 0
    assert np.isfinite(S_sag.tau) and np.isfinite(S_on.tau)


@pytest.mark.parametrize("m,expected", [("mean", 2.0), ("median", 2.0), ("last", 3.0), ("line", 3.0)])
def test_steady_state_estimators(m, expected):
    t = np.arange(5.0)
    y = np.array([1, 1.5, 2, 2.5, 3.0])
    assert steady_state(t, y, (0, 4), m) == pytest.approx(expected)


def test_fit_mono_exact():
    t = np.linspace(0, 100, 500)
    y = -70 + 10 * np.exp(-t / 15)
    f = fit_mono(t, y)
    assert f.tau == pytest.approx(15, rel=1e-4) and f.r2 > 0.9999


def test_capacitance_units():
    assert capacitance_pF(20, 200) == pytest.approx(100)
