"""Steps are ordered by injected current relative to zero, never by file position or by a single assumed step size."""
import numpy as np
import pytest

from ephys_suite import passive as P
from ephys_suite import spikes as S
from ephys_suite.report import estimate_step
from test_synthetic import make_family, make_trace, RIN


def test_smallest_hyperpolarising_step_is_the_last_before_zero_even_if_shuffled():
    t, V, I = make_family(amps=(-100, -75, -50, -25, 0, 25))
    order = [3, 0, 5, 2, 4, 1]                              # shuffle the sweeps
    r = P.analyse_passive(t, V[order], I[order], P.PassiveParams(step=(200, 1200)))
    assert r.smallest.dI == pytest.approx(-25) and r.lowest.dI == pytest.approx(-100)
    assert r.smallest.tau_used and not r.lowest.tau_used


def test_uneven_steps_are_handled():
    t, V, I = make_family(amps=(-130, -60, -20, 0, 40))
    r = P.analyse_passive(t, V, I, P.PassiveParams(step=(200, 1200)))
    assert r.rin == pytest.approx(RIN, rel=5e-3)
    assert r.smallest.dI == pytest.approx(-20)


def test_estimate_step_only_for_uniform_protocols():
    assert estimate_step([-500, -400, -300, -200, -100, 0, 100, 200]) == 100
    assert estimate_step([-500.4, -399.7, -300.9, -200.2, -100.5, 0.2, 99.8]) == 100
    assert estimate_step([-130, -60, -20, 0, 40, 200]) == 0          # uneven: do not round


def test_rheobase_orders_by_current_not_file_position():
    t, v = make_trace([100])
    quiet = np.full_like(v, -65.0)
    # sweeps in scrambled order: currents 300 (spikes), 100 (quiet), 200 (spikes), 0 (quiet)
    V = np.vstack([v, quiet, v, quiet])
    dI = np.array([300.0, 100.0, 200.0, 0.0])
    I = np.vstack([np.full_like(v, c) for c in dI])
    per, _ = S.detect_all(t, V, S.SpikeParams(window=(0, 1000)))
    assert S.order_from_zero(dI) == [1, 2, 0]
    val, sw = S.rheobase_record(per, dI)
    assert (val, sw) == (200.0, 2)
    val, sw = S.rheobase_exact(per, dI, I, np.zeros(4))
    assert sw == 2 and val == pytest.approx(200.0)


def test_rheobase_scala_sorts_by_current():
    cur = np.array([300, 0, 150, 250, 50, 100, 200.0])
    cnt = np.array([9, 0, 1, 6, 0, 0, 3.0])
    rheo, info = S.rheobase_scala(cnt, cur, 1.0)
    assert info["first_supra"] == 150 and rheo <= 150


def test_sibling_abfs_natural_order():
    import os, tempfile
    from ephys_suite.io import sibling_abfs
    d = tempfile.mkdtemp()
    for n in ("cell10.abf", "cell2.abf", "cell1.ABF", "notes.txt", ".hidden.abf", "26424030.abf"):
        open(os.path.join(d, n), "w").close()
    got = [os.path.basename(p) for p in sibling_abfs(os.path.join(d, "cell2.abf"))]
    assert got == ["26424030.abf", "cell1.ABF", "cell2.abf", "cell10.abf"]
