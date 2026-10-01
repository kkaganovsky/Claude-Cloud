"""Input resistance, membrane time constant and capacitance from current-clamp steps.

Units throughout: time ms, Vm mV, Im pA. Rin is MOhm, tau ms, Cm pF.

Method sources
- Input resistance (Easy Electrophysiology manual v2.6.3, section 9): dV and dI are the
  difference between the mean of a 'measure' region and the mean of a 'baseline' region;
  Rin is the slope of an OLS line (scipy linregress, intercept modelled) with x = dI (nA)
  and y = dV (mV). With a single record Rin = V / I.
- Mono-exponential f(x) = b0 + b1*exp(-x/tau), x = t - t[0], fitted with scipy
  least_squares (Trust Region Reflective), tau > 0 (manual, Curve Fitting + Appendix I).
- Cm = tau / Rin, as specified by the user (ref given by the user: PMC2775376).
  This is NOT in the Easy Electrophysiology manual.
"""
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats
from scipy.optimize import least_squares

ESTIMATORS = ("mean", "median", "last", "line")


@dataclass
class Params:
    # regions (ms)
    baseline: Tuple[float, float] = (0.0, 100.0)
    measure: Tuple[float, float] = (600.0, 900.0)
    step: Tuple[float, float] = (100.0, 1000.0)      # injected step start / stop
    estimator: str = "mean"          # steady-state estimator over `measure`
    last_n: int = 1                  # samples used by the 'last' estimator
    only_negative: bool = True
    min_abs_dI: float = 1.0          # pA; |dI| below this is treated as no step
    exclude_spikes: bool = True
    spike_threshold: float = 0.0     # mV
    # exponential fit
    fit_start_mode: str = "onset"    # 'onset' | 'sag' | 'custom'
    fit_start_offset: float = 0.0    # ms after step start ('onset' mode)
    fit_start_custom: float = 100.0  # ms ('custom' mode)
    sag_search: float = 200.0        # ms after step start searched for the sag peak
    fit_end: float = 1000.0          # ms
    b0_mode: str = "free"            # 'free' | 'fixed' (b0 = steady-state estimate)
    tau_agg: str = "median"          # 'median' | 'mean' across sweeps


def steady_state(t, y, window, method="mean", last_n=1) -> float:
    """Single value for y in window. 'line' = OLS line evaluated at the window end."""
    m = (t >= window[0]) & (t <= window[1])
    if not m.any():
        return float("nan")
    yy, tt = y[m], t[m]
    if method == "mean":
        return float(yy.mean())
    if method == "median":
        return float(np.median(yy))
    if method == "last":
        return float(yy[-max(1, last_n):].mean())
    if method == "line":
        if len(yy) < 2:
            return float(yy[-1])
        slope, icpt = np.polyfit(tt, yy, 1)
        return float(slope * tt[-1] + icpt)
    raise ValueError(method)


def detect_step(t, i) -> Optional[Tuple[float, float]]:
    """Find step start/stop (ms) from Im (n_sweeps, N): half-maximum crossing on the sweep
    with the largest deviation from the holding level (median of the first and last 2 %
    of the sweep, so steps longer than half the sweep still work)."""
    i = np.atleast_2d(i)
    k2 = max(1, i.shape[1] // 50)
    hold = np.median(np.concatenate([i[:, :k2], i[:, -k2:]], axis=1), axis=1, keepdims=True)
    dev = i - hold
    k = int(np.argmax(np.abs(dev).max(axis=1)))
    d = np.abs(dev[k])
    if d.max() < 1.0:   # < 1 pA: no step in the data
        return None
    idx = np.where(d > 0.5 * d.max())[0]
    dt = t[1] - t[0]
    return float(t[idx[0]]), float(t[idx[-1]] + dt)


def default_params(t, i) -> Params:
    """Params with regions placed from the detected step (baseline = pre-step, measure =
    last 25 % of the step, ending 5 % before its stop to avoid the Im transient)."""
    p = Params()
    st = detect_step(t, i)
    if st is None:
        T = float(t[-1])
        st = (0.1 * T, 0.9 * T)
    a, b = st
    L = b - a
    p.step = (a, b)
    t0 = float(t[0])
    p.baseline = (t0, a - 0.02 * L) if a - 0.02 * L > t0 else (t0, t0 + (t[1] - t[0]))
    p.measure = (b - 0.30 * L, b - 0.05 * L)
    p.fit_end = b
    p.sag_search = min(0.3 * L, 300.0)
    return p


@dataclass
class FitResult:
    ok: bool
    tau: float = np.nan          # ms
    b0: float = np.nan
    b1: float = np.nan
    r2: float = np.nan
    t: np.ndarray = field(default_factory=lambda: np.empty(0))
    y_fit: np.ndarray = field(default_factory=lambda: np.empty(0))
    msg: str = ""


def fit_mono(t, y, fixed_b0: Optional[float] = None) -> FitResult:
    """b0 + b1*exp(-(t-t0)/tau). Start values follow the manual's recipe (tau from the
    time to half amplitude / ln2) with b0 taken from the end of the window."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if len(t) < 5:
        return FitResult(False, msg="too few samples")
    x = t - t[0]
    n_end = max(1, len(y) // 10)
    b0s = float(np.mean(y[-n_end:])) if fixed_b0 is None else fixed_b0
    b1s = float(y[0] - b0s)
    half = b0s + 0.5 * b1s
    cross = np.where((y - half) * np.sign(b1s if b1s else 1) <= 0)[0]
    thalf = x[cross[0]] if len(cross) else x[-1] / 5.0
    tau0 = max(thalf / np.log(2), 1e-3 * x[-1], 1e-3)
    try:
        if fixed_b0 is None:
            fun = lambda p: p[0] + p[1] * np.exp(-x / p[2]) - y
            r = least_squares(fun, [b0s, b1s, tau0],
                              bounds=([-np.inf, -np.inf, 1e-6], [np.inf, np.inf, np.inf]),
                              method="trf")
            b0, b1, tau = r.x
        else:
            fun = lambda p: fixed_b0 + p[0] * np.exp(-x / p[1]) - y
            r = least_squares(fun, [b1s, tau0],
                              bounds=([-np.inf, 1e-6], [np.inf, np.inf]), method="trf")
            b0, (b1, tau) = fixed_b0, r.x
    except Exception as e:  # pragma: no cover
        return FitResult(False, msg=str(e))
    yf = b0 + b1 * np.exp(-x / tau)
    ss_res = float(np.sum((y - yf) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return FitResult(bool(r.success), float(tau), float(b0), float(b1), r2, t, yf)


def sag_metrics(t, v, v_base, v_ss, step_start, search_ms):
    """Sag peak (min Vm in the search window), sag = Vss - Vpeak, ratio = sag / (Vbase - Vpeak)
    (manual: sag divided by the minimum deflection from baseline). Hyperpolarizing steps."""
    m = (t >= step_start) & (t <= step_start + search_ms)
    if not m.any():
        return np.nan, np.nan, np.nan, np.nan
    k = int(np.argmin(v[m]))
    tp, vp = float(t[m][k]), float(v[m][k])
    sag = v_ss - vp
    maxdef = v_base - vp
    return tp, vp, sag, (sag / maxdef if maxdef else np.nan)


@dataclass
class SweepResult:
    idx: int
    dI: float = np.nan          # pA
    dV: float = np.nan          # mV
    v_base: float = np.nan
    v_ss: float = np.nan
    spike: bool = False
    negative: bool = False
    used: bool = False
    note: str = ""
    sag_t: float = np.nan
    sag_v: float = np.nan
    sag: float = np.nan
    sag_ratio: float = np.nan
    fit: Optional[FitResult] = None
    fit_window: Tuple[float, float] = (np.nan, np.nan)


@dataclass
class Summary:
    n: int = 0
    rin: float = np.nan         # MOhm
    intercept: float = np.nan   # mV
    r: float = np.nan
    stderr: float = np.nan
    tau: float = np.nan         # ms
    tau_sd: float = np.nan
    n_tau: int = 0
    cm: float = np.nan          # pF


def capacitance_pF(tau_ms: float, rin_mohm: float) -> float:
    """Cm = tau / Rin. ms / MOhm = nF, so x1000 for pF."""
    return float(tau_ms / rin_mohm * 1e3) if rin_mohm and rin_mohm > 0 else float("nan")


def analyze(rec, p: Params, sweeps: List[int]):
    """Returns (list[SweepResult], Summary). `sweeps` are the user-selected sweep indices."""
    t = rec.t
    results = []
    for s in range(rec.v.shape[0]):
        r = SweepResult(s)
        v, i = rec.v[s], rec.i[s]
        r.v_base = steady_state(t, v, p.baseline, "mean")
        r.v_ss = steady_state(t, v, p.measure, p.estimator, p.last_n)
        r.dV = r.v_ss - r.v_base
        r.dI = (steady_state(t, i, p.measure, "mean")
                - steady_state(t, i, p.baseline, "mean"))
        r.negative = r.dI < -p.min_abs_dI
        m = (t >= p.step[0]) & (t <= p.step[1])
        r.spike = bool(m.any() and v[m].max() > p.spike_threshold)
        r.used = s in sweeps and np.isfinite(r.dV) and np.isfinite(r.dI)
        if r.used and p.only_negative and not r.negative:
            r.used, r.note = False, "not hyperpolarizing"
        if r.used and p.exclude_spikes and r.spike:
            r.used, r.note = False, "spikes"
        if r.negative:
            r.sag_t, r.sag_v, r.sag, r.sag_ratio = sag_metrics(
                t, v, r.v_base, r.v_ss, p.step[0], p.sag_search)
        if r.used:
            r.fit, r.fit_window = _fit_sweep(t, v, r, p)
        results.append(r)
    return results, summarize(results, p)


def _fit_sweep(t, v, r: SweepResult, p: Params):
    if p.fit_start_mode == "sag" and np.isfinite(r.sag_t):
        t0 = r.sag_t
    elif p.fit_start_mode == "custom":
        t0 = p.fit_start_custom
    else:
        t0 = p.step[0] + p.fit_start_offset
    t1 = p.fit_end
    m = (t >= t0) & (t <= t1)
    fixed = r.v_ss if p.b0_mode == "fixed" else None
    return fit_mono(t[m], v[m], fixed), (t0, t1)


def summarize(results: List[SweepResult], p: Params) -> Summary:
    used = [r for r in results if r.used]
    S = Summary(n=len(used))
    if len(used) >= 2:
        x = np.array([r.dI for r in used]) / 1e3     # nA
        y = np.array([r.dV for r in used])           # mV
        if np.ptp(x) > 0:
            lr = stats.linregress(x, y)
            S.rin, S.intercept, S.r, S.stderr = lr.slope, lr.intercept, lr.rvalue, lr.stderr
    elif len(used) == 1 and used[0].dI:
        S.rin = used[0].dV / (used[0].dI / 1e3)
    taus = np.array([r.fit.tau for r in used if r.fit and r.fit.ok])
    S.n_tau = len(taus)
    if len(taus):
        S.tau = float(np.median(taus) if p.tau_agg == "median" else np.mean(taus))
        S.tau_sd = float(np.std(taus, ddof=1)) if len(taus) > 1 else np.nan
    S.cm = capacitance_pF(S.tau, S.rin)
    return S
