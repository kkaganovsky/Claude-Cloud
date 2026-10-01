"""Action potential counting and firing-pattern measures.

Detection follows the Easy Electrophysiology manual v2.6.3, section 8:
  Auto-threshold Spike  - spike amplitude, positive dV/dt, negative dV/dt (searched within `width` ms after the
                          positive crossing).
  Auto-threshold Record - Auto-threshold Spike first, then the median amplitude mid-point Vm of all detected spikes
                          becomes a straight-line threshold; each excursion above it is one spike (default in EE).
  Manual Threshold      - a spike is any excursion above a fixed Vm that falls back below it.
The numeric defaults below are NOT documented in the manual; they are this suite's own choices (see DECISIONS).

Firing-pattern measures: first-spike latency, mean ISI, SFA (divisor, local variance) per the manual / its code, and
the adaptation index and rheobase of Scala et al. 2019 (Methods + github.com/berenslab/layer4).
"""
from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .common import idx_at, robust_line_ransac, ts_ms, window_mean


@dataclass
class SpikeParams:
    method: str = "auto_record"       # 'auto_record' | 'auto_spike' | 'manual'
    amplitude: float = 30.0           # mV, peak minus Vm at the positive-dV/dt crossing
    pos_dvdt: float = 20.0            # mV/ms
    neg_dvdt: float = -10.0           # mV/ms
    width: float = 2.0                # ms to find the negative dV/dt after the positive crossing
    manual_threshold: float = 0.0     # mV
    window: Tuple[float, float] = (0.0, 1e12)   # ms, region searched (default: whole sweep; set to the step)


@dataclass
class Spike:
    sweep: int
    peak_idx: int
    peak_t: float
    peak_v: float
    start_idx: int = 0            # sample of the positive-dV/dt crossing (auto) or threshold crossing (record)
    amp: float = np.nan


def _runs(mask):
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1


def detect_auto_spike(t, v, p: SpikeParams, sweep=0) -> List[Spike]:
    dt = ts_ms(t)
    i0, i1 = idx_at(t, p.window[0]), idx_at(t, min(p.window[1], t[-1]))
    dv = np.diff(v) / dt
    ups, _ = _runs(dv >= p.pos_dvdt)
    w = max(1, int(round(p.width / dt)))
    out, last_end = [], -1
    for s in ups:
        if s < i0 or s > i1 or s <= last_end:
            continue
        seg = dv[s:s + w + 1]
        neg = np.flatnonzero(seg <= p.neg_dvdt)
        if not neg.size:
            continue
        j = s + int(neg[0])
        pk = s + int(np.argmax(v[s:j + 1]))
        amp = float(v[pk] - v[s])
        if amp < p.amplitude:
            continue
        out.append(Spike(sweep, pk, float(t[pk]), float(v[pk]), s, amp))
        last_end = j
    return out


def detect_threshold(t, v, thr, window, sweep=0) -> List[Spike]:
    i0, i1 = idx_at(t, window[0]), idx_at(t, min(window[1], t[-1]))
    seg = v[i0:i1 + 1] > thr
    ups, downs = _runs(seg)
    out = []
    for a, b in zip(ups, downs):
        a, b = a + i0, b + i0
        pk = a + int(np.argmax(v[a:b + 1]))
        out.append(Spike(sweep, pk, float(t[pk]), float(v[pk]), a, float(v[pk] - v[a])))
    return out


def detect_all(t, V, p: SpikeParams, sweeps: Optional[Sequence[int]] = None):
    """Spikes per sweep (list of lists) and the straight-line threshold used (auto_record / manual)."""
    sweeps = list(range(V.shape[0])) if sweeps is None else list(sweeps)
    per = {s: detect_auto_spike(t, V[s], p, s) for s in sweeps}
    thr = None
    if p.method == "manual":
        thr = p.manual_threshold
    elif p.method == "auto_record":
        mids = [sp.peak_v - sp.amp / 2.0 for lst in per.values() for sp in lst]
        # amplitude here is peak - Vm at crossing; the mid-point Vm is crossing Vm + amp/2
        mids = [V[sp.sweep][sp.start_idx] + sp.amp / 2.0 for lst in per.values() for sp in lst]
        thr = float(np.median(mids)) if mids else None
    if thr is not None:
        per = {s: detect_threshold(t, V[s], thr, p.window, s) for s in sweeps}
    out = [per.get(s, []) for s in range(V.shape[0])]
    return out, thr


# ----------------------------------------------------------------------------------- firing-pattern measures
def first_spike_latency(spikes: List[Spike], inj_start_ms: float, thresholds_ms: Optional[Sequence[float]] = None):
    """Manual: time from the start of the Im injection to the PEAK of the first AP. If kinetics thresholds are
    given (Allen / Scala convention), the first spike's threshold time is used instead."""
    if not spikes:
        return np.nan
    t0 = spikes[0].peak_t if thresholds_ms is None else thresholds_ms[0]
    return float(t0 - inj_start_ms)


def isis(spikes: List[Spike]):
    return np.diff([s.peak_t for s in spikes]) if len(spikes) > 1 else np.array([])


def mean_isi(spikes):
    d = isis(spikes)
    return float(d.mean()) if d.size else np.nan


def sfa_divisor(spikes):
    """First ISI divided by last ISI (manual). Needs >= 3 spikes."""
    d = isis(spikes)
    return float(d[0] / d[-1]) if len(spikes) >= 3 else np.nan


def sfa_local_variance(spikes):
    """Shinomoto et al. 2003: LV = 1/(n-1) sum 3 (ISI_i - ISI_i+1)^2 / (ISI_i + ISI_i+1)^2 (manual Appendix I)."""
    d = isis(spikes)
    if len(d) < 2:
        return np.nan
    a, b = d[:-1], d[1:]
    return float(np.sum(3 * (a - b) ** 2 / (a + b) ** 2) / len(a))


def adaptation_index_scala(spikes):
    """Scala et al. 2019: ratio of the second ISI to the first (needs >= 3 spikes)."""
    d = isis(spikes)
    return float(d[1] / d[0]) if len(d) >= 2 else np.nan


# ----------------------------------------------------------------------------------------------- rheobase
def rheobase_record(spikes_per_sweep, d_im):
    """EE 'Record': Im injection of the first record containing an AP. Returns (value, sweep) or (nan, None)."""
    for s, sp in enumerate(spikes_per_sweep):
        if sp:
            return float(d_im[s]), s
    return np.nan, None


def rheobase_exact(spikes_per_sweep, im, baselines):
    """EE 'Exact': Im at the sample of the first AP peak minus the record's Im baseline."""
    for s, sp in enumerate(spikes_per_sweep):
        if sp:
            return float(im[s][sp[0].peak_idx] - baselines[s]), s
    return np.nan, None


def rheobase_scala(spike_counts, currents, duration_s):
    """Scala et al. 2019 / layer4 notebook: robust regression of spike frequency on current over the five lowest
    depolarising currents with non-zero spike count; x-intercept restricted to [highest non-spiking current,
    lowest spiking current]. Falls back to the first spiking current when fewer than five spiking steps exist or
    when >= 3 of the five counts are identical (the notebook's RANSAC-failure proxy).
    Uses a deterministic RANSAC (common.robust_line_ransac). Returns (rheobase, info dict)."""
    cur = np.asarray(currents, float)
    cnt = np.asarray(spike_counts, float)
    pos = cur >= 0
    cur, cnt = cur[pos], cnt[pos]
    nz = np.flatnonzero(cnt)
    if nz.size == 0:
        return np.nan, {"mode": "no spikes"}
    first_supra = float(cur[nz[0]])
    sub = float(cur[nz[0] - 1]) if nz[0] > 0 else first_supra
    if nz.size < 5:
        return first_supra, {"mode": "fallback: <5 spiking steps", "first_supra": first_supra}
    idx = nz[:5]
    c5 = cnt[idx]
    if max(int(np.sum(c5 == x)) for x in c5) >= 3:
        return first_supra, {"mode": "fallback: >=3 identical counts", "first_supra": first_supra}
    x, y = cur[idx], c5 / duration_s
    m, c, inl = robust_line_ransac(x, y)
    if m <= 0:
        return first_supra, {"mode": "fallback: non-positive slope", "first_supra": first_supra}
    x_int = -c / m
    rheo = min(max(x_int, sub), first_supra)
    return float(rheo), {"mode": "ransac", "slope_hz_per_pA": m, "intercept_hz": c, "x_intercept": float(x_int),
                         "sub": sub, "first_supra": first_supra, "inliers": inl.tolist(), "x": x.tolist(),
                         "y": y.tolist()}


def round_to_step(values, step):
    """Round Im to the nearest multiple of the protocol step (simple form of EE's 'Round Im Injections')."""
    values = np.asarray(values, float)
    return values if not step else step * np.round(values / step)
