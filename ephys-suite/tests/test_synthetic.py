import numpy as np
import pytest

from ephys_suite import events as E
from ephys_suite import kinetics as K
from ephys_suite import passive as P
from ephys_suite import spikes as S
from ephys_suite.common import robust_line_ransac

FS = 20000.0
TS = 1000.0 / FS


# ---------------------------------------------------------------------------------------------- events
def synth_events(noise=4.0, n=40, amps=(-60, -15), rise=0.6, decay=6.0, seed=1, dur_s=10):
    rng = np.random.default_rng(seed)
    N = int(dur_s * FS)
    t = np.arange(N) * TS
    y = rng.normal(0, noise, N)
    times = np.sort(rng.uniform(100, t[-1] - 100, n))
    keep = [times[0]]
    for x in times[1:]:
        if x - keep[-1] > 60:
            keep.append(x)
    times = np.array(keep)
    amp = rng.uniform(*amps, len(times))
    for tm, a in zip(times, amp):
        x = t - tm
        m = x >= 0
        e = np.zeros(N)
        e[m] = (1 - np.exp(-x[m] / rise)) * np.exp(-x[m] / decay)
        y += a * e / e.max()
    return t, y, times, amp


@pytest.mark.parametrize("method", ["deconvolution", "correlation", "detection_criterion"])
def test_template_methods_find_events(method):
    t, y, times, amp = synth_events()
    res = E.analyse_events(t, y[None, :], E.EventParams(method=method, lower_value=-10.0, amplitude_threshold=8.0))
    det = np.array([e.peak_t for e in res.events])
    tp = sum(np.any(np.abs(det - (tm + 1.5)) < 4) for tm in times)
    assert tp >= 0.95 * len(times)
    assert len(res.events) - tp <= 3


def test_threshold_method_with_strict_threshold():
    t, y, times, amp = synth_events()
    p = E.EventParams(method="threshold", lower_value=-25.0, amplitude_threshold=15.0)
    res = E.analyse_events(t, y[None, :], p)
    det = np.array([e.peak_t for e in res.events])
    strong = times[amp < -35]
    assert all(np.any(np.abs(det - (tm + 1.5)) < 4) for tm in strong)


def test_event_kinetics_are_sane():
    t, y, times, amp = synth_events(noise=1.0, amps=(-40, -40))
    p = E.EventParams(average_peak_ms=0.5, lower_value=-10)
    res = E.analyse_events(t, y[None, :], p)
    S_ = E.summarize(res)
    assert S_["amplitude_median"] == pytest.approx(-40, rel=0.1)
    assert 0.6 < S_["rise_ms_median"] < 1.6
    assert S_["tau_ms_median"] == pytest.approx(6.0, rel=0.25)


def test_omit_period_is_applied_to_every_record():
    t, y, times, amp = synth_events(n=30, dur_s=5)
    Y = np.vstack([y, y])
    omit = (float(times[0]) - 5, float(times[0]) + 20)
    res = E.analyse_events(t, Y, E.EventParams(lower_value=-10, amplitude_threshold=8, omit=(omit,)))
    assert not any(omit[0] <= e.peak_t <= omit[1] for e in res.events)


def test_fit_template_recovers_rise_decay():
    x = np.arange(0, 40, TS)
    y = E.biexp_event(x, 0.0, -50.0, 0.7, 7.0)
    f = E.fit_template_to_event(x, y, -1)
    assert f["rise"] == pytest.approx(0.7, rel=0.05) and f["decay"] == pytest.approx(7.0, rel=0.05)


def test_sliding_window_matches_brute_force():
    rng = np.random.default_rng(0)
    d = rng.normal(size=300)
    tpl = np.exp(-np.arange(20) / 5.0)
    dc, scale, offset, r = E.sliding_window_fit(d, tpl)
    i = 100
    seg = d[i:i + 20]
    A = np.vstack([tpl, np.ones(20)]).T
    sc, off = np.linalg.lstsq(A, seg, rcond=None)[0]
    assert scale[i] == pytest.approx(sc) and offset[i] == pytest.approx(off)
    assert r[i] == pytest.approx(abs(np.corrcoef(tpl, seg)[0, 1]))


def test_deconvolution_peaks_at_event():
    t, y, times, amp = synth_events(noise=1.0, n=3)
    p = E.EventParams()
    m = E.compute_measure(t, y, p)
    k = int(np.argmax(m))
    assert abs(t[k] - times[np.argmax(-amp)]) < 5 or abs(t[k] - times[np.argmax(m[[int((x) / TS) for x in times]])]) < 5


def test_cdf_and_ks():
    x, p = E.cumulative_probability([3, 1, 2])
    assert list(x) == [1, 2, 3] and p[-1] == 1.0
    assert E.ks_test(np.arange(50), np.arange(50) + 100)[1] < 1e-6


# ----------------------------------------------------------------------------------------------- spikes
def ap_shape(t, t0, peak=40.0, base=-65.0):
    x = np.clip(t - t0, 0, None)
    s = (1 - np.exp(-x / 0.25)) * np.exp(-x / 0.6)
    return s / s.max()


def make_trace(spike_times, dur=1000.0):
    t = np.arange(0, dur, TS)
    v = np.full_like(t, -65.0)
    for st in spike_times:
        v += 105.0 * ap_shape(t, st) - 0  # ~ +40 mV peak
        v -= 3.0 * np.where(t > st + 2, np.exp(-(t - st - 2) / 5.0), 0)    # small AHP
    return t, v


def test_spike_counting_and_isi():
    times = [100, 200, 320, 460, 620]
    t, v = make_trace(times)
    p = S.SpikeParams(window=(0, 1000), method="auto_record")
    per, thr = S.detect_all(t, v[None, :], p)
    sp = per[0]
    assert len(sp) == 5
    np.testing.assert_allclose([s.peak_t for s in sp], np.array(times) + 0.55, atol=1.0)
    d = np.diff(times)
    assert S.mean_isi(sp) == pytest.approx(d.mean(), abs=0.2)
    assert S.sfa_divisor(sp) == pytest.approx(d[0] / d[-1], rel=0.01)
    a, b = d[:-1], d[1:]
    assert S.sfa_local_variance(sp) == pytest.approx(np.sum(3 * (a - b) ** 2 / (a + b) ** 2) / len(a), rel=0.01)
    assert S.adaptation_index_scala(sp) == pytest.approx(d[1] / d[0], rel=0.01)


def test_manual_and_auto_spike_agree_on_clean_data():
    t, v = make_trace([100, 400])
    a = S.detect_all(t, v[None, :], S.SpikeParams(method="auto_spike", window=(0, 1000)))[0][0]
    m = S.detect_all(t, v[None, :], S.SpikeParams(method="manual", manual_threshold=-10, window=(0, 1000)))[0][0]
    assert len(a) == len(m) == 2


def test_kinetics_on_synthetic_ap():
    t, v = make_trace([100])
    pk = int(np.argmax(v))
    k = K.analyse_ap(t, v, pk, K.KineticsParams())
    assert k.ok
    assert k.amplitude == pytest.approx(v[pk] - k.thr_v)
    assert -66 < k.thr_v < -30
    # half-width against the analytic waveform
    tt = np.arange(0, 6, 0.0005)
    s = (1 - np.exp(-tt / 0.25)) * np.exp(-tt / 0.6)
    s /= s.max()
    thr_frac = (k.thr_v + 65) / 105.0
    lvl = thr_frac + (1 - thr_frac) / 2
    above = tt[s >= lvl]
    assert k.half_width_ms == pytest.approx(above[-1] - above[0], abs=0.06)
    assert k.fahp < 0 and k.mahp < 0


@pytest.mark.parametrize("m", list(K.THRESHOLD_METHODS))
def test_every_threshold_method_runs(m):
    t, v = make_trace([100])
    k = K.analyse_ap(t, v, int(np.argmax(v)), K.KineticsParams(thr_method=m))
    assert k.ok or k.why == "no threshold found"


def test_rheobase_scala_ransac_with_outlier():
    cur = np.array([0, 50, 100, 150, 200, 250, 300, 350, 400.0])
    cnt = np.array([0, 0, 0, 1, 3, 6, 9, 20, 15.0])           # 5 lowest spiking steps: 150..350, 20 is an outlier-ish
    rheo, info = S.rheobase_scala(cnt, cur, 1.0)
    assert info["mode"] == "ransac"
    assert 100 <= rheo <= 150
    r2, i2 = S.rheobase_scala(np.array([0, 0, 1, 1, 1, 1, 1.0]), np.arange(7) * 50.0, 1.0)
    assert i2["mode"].startswith("fallback") and r2 == 100


def test_ransac_ignores_outlier():
    x = np.arange(6.0)
    y = 2 * x + 1
    y[3] += 30
    m, c, inl = robust_line_ransac(x, y)
    assert m == pytest.approx(2.0) and c == pytest.approx(1.0) and not inl[3]


# ----------------------------------------------------------------------------------------------- passive
RIN, TAU, VREST = 100.0, 20.0, -70.0


def make_family(amps=(-100, -75, -50, -25, 0, 25), sag=0.0):
    t = np.arange(0, 1500, TS)
    V, I = [], []
    for a in amps:
        on = (t >= 200) & (t < 1200)
        dv = a / 1e3 * RIN * (1 - np.exp(-(t - 200) / TAU))
        v = VREST + np.where(on, dv, 0.0)
        if a < 0 and sag:
            v += np.where(on, sag * np.exp(-(t - 200) / 100.0) * (1 - np.exp(-(t - 200) / 20.0)) * -a / 100.0 * -1, 0)
        V.append(v)
        I.append(np.where(on, float(a), 0.0))
    return t, np.array(V), np.array(I)


def test_passive_clean_family():
    t, V, I = make_family()
    p = P.PassiveParams(step=(200, 1200))
    r = P.analyse_passive(t, V, I, p)
    assert r.rin == pytest.approx(RIN, rel=5e-3)
    assert r.tau == pytest.approx(TAU, rel=1e-2)
    assert r.cm_pF == pytest.approx(TAU / RIN * 1e3, rel=1e-2)
    assert r.lowest.dI == pytest.approx(-100) and r.smallest.dI == pytest.approx(-25)
    assert r.lowest.tolias == pytest.approx(1.0, abs=1e-3) and r.lowest.sag_mV == pytest.approx(0, abs=1e-2)


def test_passive_methods_agree_and_ri_n():
    t, V, I = make_family()
    base = P.analyse_passive(t, V, I, P.PassiveParams(step=(200, 1200))).rin
    for m in ("ransac", "median_ratio"):
        assert P.analyse_passive(t, V, I, P.PassiveParams(step=(200, 1200), ri_method=m)).rin == pytest.approx(base, rel=1e-2)
    assert P.analyse_passive(t, V, I, P.PassiveParams(step=(200, 1200), ri_n=2)).rin_n == 2


def test_sag_definitions():
    t = np.arange(0, 1500, TS)
    on = (t >= 200) & (t < 1200)
    v = np.full_like(t, -70.0)
    v[on] = -70 - 10 * (1 - np.exp(-(t[on] - 200) / 5)) - 4 * np.exp(-(t[on] - 200) / 100) * (1 - np.exp(-(t[on] - 200) / 5))
    i = np.where(on, -100.0, 0.0)
    r = P.analyse_passive(t, v[None], i[None], P.PassiveParams(step=(200, 1200), tau_source="all")).lowest
    base, ss, pk = -70.0, r.v_ss, r.v_peak
    assert r.sag_mV == pytest.approx(pk - ss)
    assert r.sag_ratio == pytest.approx((pk - ss) / (pk - base))
    assert r.tolias == pytest.approx((pk - base) / (ss - base))
    assert r.tolias == pytest.approx(1 + r.sag_mV / (ss - base))     # identity used when back-solving the reference


def test_rounding_to_protocol_step():
    t, V, I = make_family()
    I = I * 1.007
    r = P.analyse_passive(t, V, I, P.PassiveParams(step=(200, 1200), round_step=25.0))
    assert r.lowest.dI == -100
