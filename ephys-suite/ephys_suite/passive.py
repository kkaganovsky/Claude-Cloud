"""Passive properties from a family of current steps: input resistance, sag, membrane tau, capacitance.

Input resistance (EE manual section 9): dV = mean(measure) - mean(baseline), dI likewise from Im; hyperpolarising
steps; OLS line of dV (mV) on dI (nA), slope = MOhm. Alternatives (Scala et al. 2019 Methods): RANSAC regression of
the steady-state voltage on current over the five most hyperpolarising steps, or the median of per-step dV/dI.
Sag (EE): sag = Vpeak - Vss (negative for hyperpolarising steps), sag ratio = sag / (Vpeak - Vbaseline).
'Tolias ratio' (Scala et al. 'sag ratio'): (Vpeak - Vbaseline) / (Vss - Vbaseline). Both from the lowest (most
hyperpolarising) step.
Tau: mono-exponential b0 + b1 exp(-t/tau). Cm = tau / Rin (user-specified method; see Golowasch et al. 2009 for the
caveat that this assumes an isopotential cell).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats

from .common import fit_monoexp, idx_at, robust_line_ransac, ts_ms, window_mean


@dataclass
class PassiveParams:
    step: Tuple[float, float] = (0.0, 1000.0)
    baseline_ms: float = 100.0                  # window before step start
    measure_ms: float = 100.0                   # window ending at step stop
    baseline_region: Optional[Tuple[float, float]] = None   # explicit region overrides baseline_ms
    measure_region: Optional[Tuple[float, float]] = None
    peak_avg_ms: float = 0.0                    # 0 = raw minimum sample (EE); Allen/Scala averages 5 ms
    min_abs_dI: float = 1.0                     # pA
    exclude_spikes: bool = True
    spike_threshold: float = 0.0                # mV
    ri_method: str = "ols"                      # 'ols' | 'ransac' | 'median_ratio'
    ri_n: Optional[int] = None                  # use the n most hyperpolarising steps (Scala: 5); None = all
    round_step: float = 0.0                     # round dI to this step (pA); 0 = measured
    tau_source: str = "smallest"                # 'smallest' hyperpolarising step | 'all' (median)
    fit_start_mode: str = "onset"               # 'onset' | 'ten_percent' (Allen) | 'custom'
    fit_start_offset: float = 0.0               # ms after step start (onset mode)
    fit_start_custom: float = 0.0
    fit_end_mode: str = "step_end"              # 'step_end' | 'peak' | 'custom'
    fit_end_custom: float = 0.0
    b0_mode: str = "free"                       # 'free' | 'fixed' (b0 = steady state)

    def regions(self):
        a, b = self.step
        base = self.baseline_region or (a - self.baseline_ms, a)
        meas = self.measure_region or (b - self.measure_ms, b)
        return base, meas


@dataclass
class SweepPassive:
    idx: int
    dI: float = np.nan
    v_base: float = np.nan
    v_ss: float = np.nan
    dV: float = np.nan
    v_peak: float = np.nan
    t_peak: float = np.nan
    sag_mV: float = np.nan
    sag_ratio: float = np.nan       # EE: sag / (Vpeak - Vbase)
    tolias: float = np.nan          # Scala 'sag ratio': (Vpeak - Vbase) / (Vss - Vbase)
    negative: bool = False
    spike: bool = False
    used: bool = False
    note: str = ""
    fit: Optional[dict] = None
    fit_window: Tuple[float, float] = (np.nan, np.nan)
    tau_used: bool = False


@dataclass
class PassiveResult:
    sweeps: List[SweepPassive]
    rin: float = np.nan
    rin_intercept: float = np.nan
    rin_r2: float = np.nan
    rin_n: int = 0
    tau: float = np.nan
    cm_pF: float = np.nan
    lowest: Optional[SweepPassive] = None       # most hyperpolarising used step
    smallest: Optional[SweepPassive] = None     # least hyperpolarising used step


def analyse_passive(t, V, I, p: PassiveParams, sweeps=None) -> PassiveResult:
    sweeps = list(range(V.shape[0])) if sweeps is None else list(sweeps)
    base, meas = p.regions()
    out = []
    a, b = p.step
    for s in range(V.shape[0]):
        r = SweepPassive(s)
        v, i = V[s], I[s]
        r.v_base = window_mean(t, v, *base)
        r.v_ss = window_mean(t, v, *meas)
        r.dV = r.v_ss - r.v_base
        r.dI = window_mean(t, i, *meas) - window_mean(t, i, *base)
        if p.round_step:
            r.dI = p.round_step * round(r.dI / p.round_step)
        r.negative = r.dI < -p.min_abs_dI
        i0, i1 = idx_at(t, a), idx_at(t, b)
        seg = v[i0:i1 + 1]
        r.spike = bool(seg.max() > p.spike_threshold)
        k = i0 + int(np.argmin(seg))
        if p.peak_avg_ms > 0:
            h = int(round(p.peak_avg_ms / 2 / ts_ms(t)))
            r.v_peak = float(v[max(0, k - h):k + h + 1].mean())
        else:
            r.v_peak = float(v[k])
        r.t_peak = float(t[k])
        if r.negative:
            r.sag_mV = r.v_peak - r.v_ss
            r.sag_ratio = r.sag_mV / (r.v_peak - r.v_base) if r.v_peak != r.v_base else np.nan
            r.tolias = (r.v_peak - r.v_base) / (r.v_ss - r.v_base) if r.v_ss != r.v_base else np.nan
        r.used = s in sweeps and r.negative
        if s in sweeps and not r.negative:
            r.note = "not hyperpolarizing"
        if r.used and p.exclude_spikes and r.spike:
            r.used, r.note = False, "spikes"
        out.append(r)
    used = [r for r in out if r.used]
    res = PassiveResult(out)
    if not used:
        return res
    by_dI = sorted(used, key=lambda r: r.dI)              # most negative first
    res.lowest, res.smallest = by_dI[0], by_dI[-1]
    ri_set = by_dI[:p.ri_n] if p.ri_n else by_dI
    x = np.array([r.dI for r in ri_set]) / 1e3
    y = np.array([r.dV for r in ri_set])
    res.rin_n = len(ri_set)
    if len(ri_set) == 1:
        res.rin = float(y[0] / x[0])
    elif p.ri_method == "median_ratio":
        res.rin = float(np.median(y / x))
    elif p.ri_method == "ransac":
        m, c, _ = robust_line_ransac(x, y)
        res.rin, res.rin_intercept = m, c
    else:
        lr = stats.linregress(x, y)
        res.rin, res.rin_intercept, res.rin_r2 = float(lr.slope), float(lr.intercept), float(lr.rvalue ** 2)
    tau_sweeps = [res.smallest] if p.tau_source == "smallest" else used
    taus = []
    for r in used:
        w = _fit_window(t, V[r.idx], r, p)
        r.fit_window = w
        m = (t >= w[0]) & (t <= w[1])
        if m.sum() >= 5:
            r.fit = fit_monoexp(t[m], V[r.idx][m], r.v_ss if p.b0_mode == "fixed" else None)
    for r in tau_sweeps:
        r.tau_used = True
        if r.fit and r.fit["ok"]:
            taus.append(r.fit["tau"])
    if taus:
        res.tau = float(np.median(taus))
        res.cm_pF = res.tau / res.rin * 1e3 if res.rin > 0 else np.nan
    return res


def _fit_window(t, v, r: SweepPassive, p: PassiveParams):
    a, b = p.step
    if p.fit_start_mode == "ten_percent":
        i0 = idx_at(t, a)
        defl = r.v_peak - r.v_base
        c = np.flatnonzero(v[i0:] <= 0.1 * defl + r.v_base)
        t0 = float(t[i0 + c[0]]) if c.size else a
    elif p.fit_start_mode == "custom":
        t0 = p.fit_start_custom
    else:
        t0 = a + p.fit_start_offset
    t1 = {"step_end": b, "peak": r.t_peak, "custom": p.fit_end_custom}[p.fit_end_mode]
    return t0, t1


def rmp(v, t=None, region=None) -> float:
    """Resting membrane potential: mean Vm of a trace (optionally a region in ms)."""
    v = np.asarray(v, float)
    if region is None or t is None:
        return float(v.mean())
    return window_mean(t, v, *region)
