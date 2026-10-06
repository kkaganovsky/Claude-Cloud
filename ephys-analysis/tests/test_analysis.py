import numpy as np
import pytest

from ephys_analysis.analysis import (analyze, capacitance_pF, default_params,
                                     copy_row, detect_step, estimate_step, fit_mono, steady_state,
                                     step_currents)
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
    assert S.n == 6                                   # 5 hyperpolarizing + the 0 pA sweep; +20 pA excluded
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
    assert not res[0].used and res[0].note == "spikes" and S.n == 5


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


def test_biexp_r0_recovers_cm_of_two_compartment_response():
    # Vm = Vrest + V0(1-e^-t/tau0) + V1(1-e^-t/tau1), tau0 = 100 ms, V0 = -8 mV, I = -100 pA
    # => R0 = 80 MOhm, Cm = tau0/R0 = 1250 pF; Rin = (8+4)/0.1 = 120 MOhm, tau0/Rin = 833 pF
    t = np.arange(0, 1500, 0.1)
    on = t >= 200
    x = np.where(on, t - 200, 0)
    vm = -65 + on * (-8 * (1 - np.exp(-x / 100)) - 4 * (1 - np.exp(-x / 10)))
    vm = np.where(t < 1200, vm, -65.0)
    cur = np.where((t >= 200) & (t < 1200), -100.0, 0.0)
    v2 = np.vstack([vm, 0.5 * (vm + 65) - 65])      # second sweep: -50 pA
    c2 = np.vstack([cur, 0.5 * cur])
    rec = Recording("syn2", t, v2, c2, "syn", [])
    p = default_params(t, c2)
    p.n_exp = 2
    res, S = analyze(rec, p, [0, 1])
    assert S.tau == pytest.approx(100, rel=0.02)
    assert S.r0 == pytest.approx(80, rel=0.02)
    assert S.cm_r0 == pytest.approx(1250, rel=0.03)
    assert S.cm == pytest.approx(833, rel=0.03)


def test_tau_from_smallest_hyperpolarizing_sweep_only():
    rec = make_rec()
    # give the smallest hyperpolarizing sweep (-20 pA, idx 4) a different tau
    tt = rec.t
    dv = -20 / 1e3 * RIN * (1 - np.exp(-(tt - 200) / 40.0))
    rec.v[4] = VREST + np.where((tt >= 200) & (tt < 1200), dv, 0.0)
    p = default_params(rec.t, rec.i)
    res, S = analyze(rec, p, list(range(len(rec.v))))
    assert [r.idx for r in res if r.tau_used] == [4]
    assert S.tau == pytest.approx(40.0, rel=0.02)
    assert S.n == 6 and S.rin == pytest.approx(RIN, rel=5e-3)       # Rin still from all five + 0 pA
    p.tau_source = "all"
    _, S_all = analyze(rec, p, list(range(len(rec.v))))
    assert S_all.n_tau == 5 and S_all.tau == pytest.approx(20.0, rel=0.02)


def test_zero_sweep_option():
    rec = make_rec()
    p = default_params(rec.t, rec.i)
    res, S = analyze(rec, p, list(range(len(rec.v))))
    z = res[5]
    assert z.zero and z.used and not z.tau_used and z.fit is None and not np.isfinite(z.sag)
    assert S.rin == pytest.approx(RIN, rel=5e-3)
    assert not res[6].used and res[6].note == "depolarizing"      # +20 pA only in the '+ positive' mode


def test_positive_mode_adds_two_smallest_non_spiking_steps():
    rec = make_rec(amps=(-40, -20, 0, 20, 40, 60, 80))
    rec.v[3, 4000] = 30.0                       # +20 pA spikes -> skipped
    p = default_params(rec.t, rec.i)
    res, S = analyze(rec, p, list(range(len(rec.v))))
    assert [r.idx for r in res if r.used] == [0, 1, 2] and S.n == 3
    p.rin_sweeps = "neg_zero_pos"
    res, S = analyze(rec, p, list(range(len(rec.v))))
    assert [r.idx for r in res if r.used] == [0, 1, 2, 4, 5]   # +40 and +60; +20 spikes, +80 is the 3rd
    assert res[3].note == "depolarizing, spikes" and "not among 2" in res[6].note
    assert S.rin == pytest.approx(RIN, rel=5e-3)
    assert [r.idx for r in res if r.tau_used] == [1]           # tau still from the smallest hyperpolarizing step
    _, S2 = analyze(rec, p, [0, 1, 2, 4])                      # unchecked sweeps are never added
    assert S2.n == 4


def test_zero_sweep_gives_two_step_fit_a_residual():
    # two steps + 0 pA: a drifting 0 pA sweep (dV = +3 mV) shows up as R^2 < 1 instead of being invisible
    rec = make_rec(amps=(-40, -20, 0))
    rec.v[2] += np.where(rec.t > 700, 3.0, 0.0)
    p = default_params(rec.t, rec.i)
    _, S2 = analyze(rec, p, [0, 1])
    assert S2.n == 2 and S2.r ** 2 == pytest.approx(1.0)
    _, S3 = analyze(rec, p, [0, 1, 2])
    assert S3.n == 3 and S3.r ** 2 < 0.999


def test_step_labels_use_window_means_not_noise_peaks():
    # Im with a -15 pA holding offset, 2 pA RMS noise and a 1 % gain error: the labels must still read the protocol
    amps = (-40, -20, 0, 20, 40, 60)
    rec = make_rec(amps=amps)
    rng = np.random.default_rng(3)
    rec.i[:] = rec.i * 1.01 - 15.0 + rng.normal(0, 2.0, rec.i.shape)
    p = default_params(rec.t, rec.i)
    assert p.round_step == 20.0
    assert list(step_currents(rec.t, rec.i, p)) == list(map(float, amps))
    p.round_step = 0.0
    d = step_currents(rec.t, rec.i, p)
    assert np.allclose(d, np.array(amps) * 1.01, atol=0.3)


def test_estimate_step():
    assert estimate_step([-40.6, -20.0, 0.2, 20.3, 39.6]) == 20.0
    assert estimate_step([-19.9, -10.1, 0.2, 10.3]) == 10.0
    assert estimate_step([-100, -50, -40, 0]) == 0.0           # uneven protocol: do not round


def test_copy_row_is_three_tab_separated_cells():
    rec = make_rec()
    p = default_params(rec.t, rec.i)
    _, S = analyze(rec, p, list(range(len(rec.v))))
    cells = copy_row("/data/cell 3/26827002.abf", S).split("\t")
    assert cells[0] == "26827002.abf" and len(cells) == 3
    assert float(cells[1]) == pytest.approx(RIN, rel=5e-3) and float(cells[2]) == pytest.approx(100, rel=1e-2)
    from ephys_analysis.analysis import Summary
    assert copy_row("x.abf", Summary()) == "x.abf\t\t"        # nothing computed: empty cells, not 'nan'


def test_default_windows():
    rec = make_rec()                       # step 200-1200 ms
    p = default_params(rec.t, rec.i)
    assert p.measure[1] == pytest.approx(1199.0, abs=0.2) and p.measure[0] == pytest.approx(1099.0, abs=0.2)
    assert p.fit_end == pytest.approx(700.0, abs=0.2)


def test_state_roundtrip_reproduces_results(tmp_path=None):
    import json, os, tempfile
    from ephys_analysis import state as ST
    d = tmp_path or tempfile.mkdtemp()
    abf = os.path.join(str(d), "cell.abf")
    with open(abf, "wb") as f:
        f.write(b"stand-in bytes")         # the state file only hashes the data file
    rec = make_rec(noise=0.2)
    p = default_params(rec.t, rec.i)
    p.n_exp, p.estimator, p.rin_sweeps, p.measure = 2, "median", "neg_zero_pos", (1000.0, 1150.0)
    sweeps = [0, 1, 3, 4, 6]                # 6 = +20 pA, used in the '+ positive' mode
    _, S = analyze(rec, p, sweeps)
    js = ST.sidecar_path(abf)
    assert js.endswith("cell.ephys-analysis.json")
    ST.save_state(js, abf, p, sweeps, S)
    st = ST.read_state(js)
    json.dumps(st)                         # plain JSON (no NaN)
    assert ST.resolve_abf(st, js) == os.path.normpath(abf)
    assert st["abf"]["sha256"] == ST.file_sha256(abf)
    p2 = ST.params_from_dict(st["params"])
    assert p2 == p and isinstance(p2.measure, tuple)
    _, S2 = analyze(rec, p2, st["sweeps_analysed"])
    assert ST.compare_results(st["results"], S2) == []
    p2.rin_sweeps = "neg_zero"              # a different setting is detected
    _, S3 = analyze(rec, p2, sweeps)
    assert ST.compare_results(st["results"], S3)


def test_state_ignores_unknown_and_missing_keys():
    from ephys_analysis import state as ST
    p = ST.params_from_dict({"n_exp": 2, "some_future_option": 1})
    assert p.n_exp == 2 and p.estimator == "mean"


def test_tau_from_chosen_sweep():
    rec = make_rec()                          # -100 .. -20, 0, +20 ; sweep 0 gets a different tau
    tt = rec.t
    dv = -100 / 1e3 * RIN * (1 - np.exp(-(tt - 200) / 35.0))
    rec.v[0] = VREST + np.where((tt >= 200) & (tt < 1200), dv, 0.0)
    p = default_params(rec.t, rec.i)
    p.tau_source, p.tau_sweep = "sweep", 0
    res, S = analyze(rec, p, list(range(len(rec.v))))
    assert [r.idx for r in res if r.tau_used] == [0] and S.n_tau == 1 and S.tau_note == ""
    assert S.tau == pytest.approx(35.0, rel=0.02)
    assert S.cm == pytest.approx(S.tau / S.rin * 1000.0)       # Cm [pF] = tau [ms] / Rin [MOhm] * 1000
    p.tau_sweep = 5                                             # the 0 pA sweep: no tau, and the reason is given
    _, S = analyze(rec, p, list(range(len(rec.v))))
    assert not np.isfinite(S.tau) and "0 pA" in S.tau_note and not np.isfinite(S.cm)
    p.tau_sweep = 1
    _, S = analyze(rec, p, [0, 2, 3])                            # chosen sweep unchecked
    assert not np.isfinite(S.tau) and "not analysed" in S.tau_note


def test_cm_units():
    # 20 ms / 200 MOhm = 0.1 nF = 100 pF  (ms / MOhm = 1e-3 s / 1e6 Ohm = 1e-9 F)
    assert capacitance_pF(20.0, 200.0) == pytest.approx(100.0)
    assert capacitance_pF(55.6, 539.0) == pytest.approx(103.15, abs=0.01)


def test_sibling_abfs_natural_order():
    import os, tempfile
    from ephys_analysis.io import sibling_abfs
    d = tempfile.mkdtemp()
    for n in ("cell10.abf", "cell2.abf", "cell1.ABF", "notes.txt", ".hidden.abf", "26424030.abf"):
        open(os.path.join(d, n), "w").close()
    got = [os.path.basename(p) for p in sibling_abfs(os.path.join(d, "cell2.abf"))]
    assert got == ["26424030.abf", "cell1.ABF", "cell2.abf", "cell10.abf"]
