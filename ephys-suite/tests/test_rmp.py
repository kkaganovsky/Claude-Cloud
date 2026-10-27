"""Resting membrane potential with broad spike/event exclusion."""
import numpy as np
import pytest

from ephys_suite.passive import RmpParams, rmp, rmp_clean


def trace(seed=0, dur_ms=5000.0, dt=0.05, base=-40.0, drift=1.0):
    t = np.arange(0, dur_ms, dt)
    rng = np.random.default_rng(seed)
    slow = drift * np.sin(2 * np.pi * t / 1700.0)
    return t, base + slow + rng.normal(0, 0.15, len(t))


def add_event(t, v, t0, amp, rise=3.0, decay=60.0, ahp=0.0, ramp=0.0):
    """Fast rise over `rise` ms then exponential decay, optional afterhyperpolarisation and pre-spike ramp."""
    v = v.copy()
    r = (t >= t0) & (t < t0 + rise)
    v[r] += amp * (t[r] - t0) / rise
    d = t >= t0 + rise
    v[d] += amp * np.exp(-(t[d] - t0 - rise) / decay)
    v[d] -= ahp * np.exp(-(t[d] - t0 - rise) / (3 * decay)) * (1 - np.exp(-(t[d] - t0 - rise) / 5.0))
    if ramp:
        m = (t >= t0 - 40) & (t < t0)
        v[m] += ramp * (t[m] - (t0 - 40)) / 40.0
    return v


def test_plain_trace_is_untouched():
    t, v = trace()
    r = rmp_clean(t, v)
    assert r.events == [] and r.pct_excluded == 0 and r.rmp == pytest.approx(v.mean())
    assert rmp(v, t) == pytest.approx(v.mean())
    assert rmp(v) == pytest.approx(v.mean())                  # no time axis: plain mean


def test_small_spikes_that_never_reach_zero_are_excluded():
    t, v0 = trace()
    v = v0
    for t0, amp in ((800, 28), (2400, 20), (3900, 32)):       # peaks near -12..-8 mV, well below 0
        v = add_event(t, v, t0, amp, ahp=3.0, ramp=3.0)
    r = rmp_clean(t, v)
    assert len(r.events) == 3 and r.pct_excluded > 5
    assert abs(r.rmp - v0.mean()) < 0.25                      # contamination removed (rise, decay and AHP)
    assert abs(r.plain - v0.mean()) > 0.6                     # the plain mean is biased
    assert (~r.keep)[(t > 800) & (t < 900)].all()             # rising phase and early decay are inside the excluded window


def test_full_action_potentials_and_region():
    t, v0 = trace(seed=1)
    v = add_event(t, v0, 1000, 70, rise=1.0, decay=1.5, ahp=8.0, ramp=4.0)
    r = rmp_clean(t, v)
    assert len(r.events) == 1 and abs(r.rmp - v0.mean()) < 0.25
    away = rmp(v, t, region=(2000.0, 5000.0))
    assert away == pytest.approx(v[(t >= 2000) & (t < 5000)].mean(), abs=1e-2)     # no event inside the region: plain mean of the region


def test_slow_waves_are_not_events_and_exclusion_can_be_turned_off():
    t, v = trace(drift=6.0)                                   # slow +-6 mV wave: no fast rise
    assert rmp_clean(t, v).events == []
    t2, v2 = trace()
    v2 = add_event(t2, v2, 1500, 25)
    assert rmp_clean(t2, v2, p=RmpParams(exclude_events=False)).rmp == pytest.approx(v2.mean())
    assert len(rmp_clean(t2, v2).events) == 1


def test_nearly_all_excluded_falls_back_to_plain_mean():
    t, v = trace(dur_ms=400.0)
    v = add_event(t, v, 30, 30, decay=1e5)                    # never recovers: > 90 % would be excluded
    r = rmp_clean(t, v)
    assert r.keep.all() and r.rmp == pytest.approx(v.mean())


def test_synaptic_potentials_are_not_excluded():
    t, v0 = trace(seed=3)
    v = v0
    for t0 in (700, 1900, 3300):                                  # EPSP-like: fast 3-5 mV rise, decays in tens of ms
        v = add_event(t, v, t0, 4.0, rise=2.0, decay=25.0)
    r = rmp_clean(t, v)
    assert r.events == [] and r.pct_excluded == 0 and r.rmp == pytest.approx(v.mean())
    # lowering min_amp_mV brings the old behaviour back
    assert len(rmp_clean(t, v, p=RmpParams(min_amp_mV=2.0)).events) == 3
    # a spike on the same trace is still caught
    v = add_event(t, v, 2600, 40.0, rise=1.5, decay=2.0, ahp=3.0)
    assert len(rmp_clean(t, v).events) == 1
