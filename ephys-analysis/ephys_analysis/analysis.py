"""Input resistance, membrane time constant and capacitance from current-clamp steps.

Units throughout: time ms, Vm mV, Im pA. Rin is MOhm, tau ms, Cm pF.

Method sources
- Input resistance (Easy Electrophysiology manual v2.6.3, section 9): dV and dI are the
  difference between the mean of a 'measure' region and the mean of a 'baseline' region;
  Rin is the slope of an OLS line (scipy linregress, intercept modelled) with x = dI (nA)
  and y = dV (mV). With a single record Rin = V / I.
- Mono-exponential f(x) = b0 + b1*exp(-x/tau), x = t - t[0], fitted with scipy
  least_squares (Trust Region Reflective), tau > 0 (manual, Curve Fitting + Appendix I).
- Multi-exponential (manual Appendix I): b0 + sum_k b_k exp(-x/tau_k); starting taus 0.1tau,
  0.9tau (bi). Triexponential here uses 0.1/0.5/1.5 tau instead of the manual's identical tau/3
  (identical start values make the Jacobian degenerate).
- Cm (Golowasch et al. 2009, J Neurophysiol 102:2161, the paper supplied by the user):
  Vm(t) = Vrest + sum_i V_i (1 - exp(-t/tau_i)), t = 0 at step onset, fitted to steady state.
  For an isopotential cell (one exponential) Cm = tau_m / Rin. For a non-isopotential cell the
  slowest term is tau_m = tau_0 and the correct resistance is R_0 = V_0 / I_ext (amplitude of the
  slowest term), NOT Rin: Cm = tau_0 / R_0. Both are reported.
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
    rin_sweeps: str = "neg_zero"     # sweeps used for Rin (the 0 pA sweep is always included):
                                     # 'neg_zero'     = hyperpolarizing steps + 0 pA
                                     # 'neg_zero_pos' = ... + the n_pos smallest depolarizing steps without spikes
    n_pos: int = 2
    min_abs_dI: float = 1.0          # pA; |dI| below this is treated as no step (the 0 pA sweep)
    round_step: float = 0.0          # pA; round dI to this protocol step (EE 'Round Im injections'); 0 = measured
    exclude_spikes: bool = True
    spike_threshold: float = 0.0     # mV
    # exponential fit
    fit_start_mode: str = "onset"    # 'onset' | 'sag' | 'custom'
    fit_start_offset: float = 0.0    # ms after step start ('onset' mode)
    fit_start_custom: float = 100.0  # ms ('custom' mode)
    sag_search: float = 200.0        # ms after step start searched for the sag peak
    fit_end: float = 1000.0          # ms
    n_exp: int = 1                   # number of exponential terms (1-3)
    b0_mode: str = "free"            # 'free' | 'fixed' (b0 = steady-state estimate)
    tau_agg: str = "median"          # 'median' | 'mean' across the sweeps tau is taken from
    tau_source: str = "smallest"     # 'smallest' = only the smallest hyperpolarizing sweep (the one
                                     # just before injected current = 0); 'all' = every analysed
                                     # hyperpolarizing sweep; 'sweep' = the sweep chosen in tau_sweep.
                                     # Rin always uses all analysed sweeps.
    tau_sweep: int = -1              # sweep index for tau_source == 'sweep'


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


def estimate_step(dI) -> float:
    """Protocol step (pA) from measured step currents: median spacing of the sorted values, to the
    nearest 5 pA. 0 (= do not round) when the spacing is not uniform within 20 %."""
    d = np.diff(np.sort(np.asarray(dI, float)))
    d = d[d > 1.0]
    if not d.size:
        return 0.0
    med = float(np.median(d))
    if (d.max() - d.min()) > 0.2 * med:
        return 0.0
    return float(5 * round(med / 5))


def measured_dI(t, i, p) -> np.ndarray:
    """Per-sweep dI (pA) = mean Im in the measure region - mean Im in the baseline region
    (window means, so noise and a constant holding-current offset cancel)."""
    i = np.atleast_2d(i)
    return np.array([steady_state(t, row, p.measure, "mean") - steady_state(t, row, p.baseline, "mean")
                     for row in i])


def step_currents(t, i, p) -> np.ndarray:
    """Per-sweep injected step current (pA): measured dI, rounded to p.round_step when set."""
    d = measured_dI(t, i, p)
    return p.round_step * np.round(d / p.round_step) if p.round_step else d


MEASURE_GAP_MS = 1.0     # measure window ends this long before the step offset (stays clear of the off-transient)
FIT_WINDOW_MS = 500.0    # default exponential fit window: step onset -> onset + 500 ms


def default_params(t, i) -> Params:
    """Params with regions placed from the detected step (baseline = pre-step, measure =
    100 ms ending 1 ms before the step offset (Scala et al. 2019 Methods use the last 100 ms),
    exponential fit window = step onset to onset + 500 ms, capped at the step offset)."""
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
    end = max(a, b - MEASURE_GAP_MS)
    p.measure = (max(a, end - 100.0), end)   # 100 ms ending 1 ms before step offset (Scala et al. 2019: last 100 ms)
    p.fit_end = min(a + FIT_WINDOW_MS, b)     # fit the first 500 ms of the step (end is draggable)
    p.sag_search = min(0.3 * L, 300.0)
    p.round_step = estimate_step(measured_dI(t, i, p))   # 0 if the protocol is not uniform
    return p


@dataclass
class FitResult:
    ok: bool
    tau: float = np.nan          # ms; slowest time constant (tau_0 of the paper)
    b0: float = np.nan
    b1: float = np.nan           # amplitude of the slowest term
    r2: float = np.nan
    t: np.ndarray = field(default_factory=lambda: np.empty(0))
    y_fit: np.ndarray = field(default_factory=lambda: np.empty(0))
    taus: tuple = ()             # all taus, slowest first
    amps: tuple = ()             # matching amplitudes b_k (Vm = b0 + sum b_k exp(-x/tau_k))
    msg: str = ""


def fit_exp(t, y, n_exp: int = 1, fixed_b0: Optional[float] = None) -> FitResult:
    """b0 + sum_k b_k*exp(-(t-t0)/tau_k), k = 1..n_exp (1-3), scipy least_squares (TRF),
    tau > 0. Start values: b0 from the end of the window, tau from time-to-half-amplitude / ln2."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    n = int(n_exp)
    if len(t) < 3 * n + 2:
        return FitResult(False, msg="too few samples")
    x = t - t[0]
    n_end = max(1, len(y) // 10)
    b0s = float(np.mean(y[-n_end:])) if fixed_b0 is None else fixed_b0
    d0 = float(y[0] - b0s)
    half = b0s + 0.5 * d0
    cross = np.where((y - half) * np.sign(d0 if d0 else 1) <= 0)[0]
    thalf = x[cross[0]] if len(cross) else x[-1] / 5.0
    tau0 = max(thalf / np.log(2), 1e-3 * x[-1], 1e-3)
    mult = {1: [1.0], 2: [0.1, 0.9], 3: [0.1, 0.5, 1.5]}[n]
    free0 = fixed_b0 is None
    p0 = ([b0s] if free0 else []) + [d0 / n] * n + [tau0 * m for m in mult]
    lo = ([-np.inf] if free0 else []) + [-np.inf] * n + [1e-6] * n
    hi = [np.inf] * len(p0)

    def unpack(q):
        b0 = q[0] if free0 else fixed_b0
        q = q[1:] if free0 else q
        return b0, q[:n], q[n:]

    def model(q):
        b0, amps, taus = unpack(q)
        return b0 + sum(a * np.exp(-x / tk) for a, tk in zip(amps, taus))

    try:
        r = least_squares(lambda q: model(q) - y, p0, bounds=(lo, hi), method="trf")
    except Exception as e:  # pragma: no cover
        return FitResult(False, msg=str(e))
    b0, amps, taus = unpack(r.x)
    order = np.argsort(taus)[::-1]
    taus, amps = taus[order], amps[order]
    yf = model(r.x)
    ss_res = float(np.sum((y - yf) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return FitResult(bool(r.success), float(taus[0]), float(b0), float(amps[0]), r2, t, yf,
                     tuple(map(float, taus)), tuple(map(float, amps)))


def fit_mono(t, y, fixed_b0: Optional[float] = None) -> FitResult:
    return fit_exp(t, y, 1, fixed_b0)


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
    zero: bool = False          # the 0 pA sweep (|dI| <= min_abs_dI)
    positive: bool = False
    used: bool = False
    note: str = ""
    sag_t: float = np.nan
    sag_v: float = np.nan
    sag: float = np.nan
    sag_ratio: float = np.nan
    fit: Optional[FitResult] = None
    tau_used: bool = False      # this sweep's tau / R0 feeds the reported tau and Cm
    r0: float = np.nan          # MOhm; V_0 / I_ext of the slowest term (paper)
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
    cm: float = np.nan          # pF; tau_slow / Rin (isopotential assumption)
    r0: float = np.nan          # MOhm; aggregate of per-sweep R0
    cm_r0: float = np.nan       # pF; tau_0 / R0 (Golowasch 2009, non-isopotential)
    tau_note: str = ""          # why no tau, when the chosen tau sweep cannot be used


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
        if p.round_step:
            r.dI = p.round_step * round(r.dI / p.round_step)
        r.negative = r.dI < -p.min_abs_dI
        r.zero = abs(r.dI) <= p.min_abs_dI
        r.positive = r.dI > p.min_abs_dI
        m = (t >= p.step[0]) & (t <= p.step[1])
        r.spike = bool(m.any() and v[m].max() > p.spike_threshold)
        r.used = s in sweeps and np.isfinite(r.dV) and np.isfinite(r.dI)
        if r.used and r.positive:      # depolarizing steps are added below, only if the mode asks for them
            r.used, r.note = False, "depolarizing"
        if r.used and p.exclude_spikes and r.spike:
            r.used, r.note = False, "spikes"
        if r.negative:
            r.sag_t, r.sag_v, r.sag, r.sag_ratio = sag_metrics(
                t, v, r.v_base, r.v_ss, p.step[0], p.sag_search)
        if r.used and not r.zero:     # no step on the 0 pA sweep: nothing to fit
            r.fit, r.fit_window = _fit_sweep(t, v, r, p)
            if r.fit and r.fit.ok and r.dI:
                # Vm = Vrest + sum V_i(1 - e^-t/tau_i)  <=>  b_i = -V_i
                r.r0 = (-r.fit.b1) / (r.dI / 1e3)
        results.append(r)
    if p.rin_sweeps == "neg_zero_pos":
        # the n_pos smallest depolarizing steps (by current) that have no spike; spiking ones are skipped
        cand = sorted((r for r in results if r.positive and r.idx in sweeps
                       and np.isfinite(r.dV) and np.isfinite(r.dI)), key=lambda r: r.dI)
        picked = [r for r in cand if not r.spike][:max(0, int(p.n_pos))]
        for r in cand:
            if r in picked:
                r.used, r.note = True, ""
                r.fit, r.fit_window = _fit_sweep(t, rec.v[r.idx], r, p)
            else:
                r.note = "depolarizing, spikes" if r.spike else f"depolarizing (not among {p.n_pos} smallest)"
    tau_note = ""
    if p.tau_source == "sweep":
        # the user-chosen sweep: any analysed sweep with a step (not the 0 pA sweep) whose fit converged
        r = results[p.tau_sweep] if 0 <= p.tau_sweep < len(results) else None
        if r is None:
            src, tau_note = [], f"τ sweep {p.tau_sweep} does not exist"
        elif not r.used:
            src, tau_note = [], f"τ sweep {r.idx} is not analysed ({r.note or 'unchecked'})"
        elif r.zero:
            src, tau_note = [], f"τ sweep {r.idx} is the 0 pA sweep (no step to fit)"
        elif not (r.fit and r.fit.ok):
            src, tau_note = [], f"τ sweep {r.idx}: exponential fit failed"
        else:
            src = [r]
    else:
        src = [r for r in results if r.used and r.negative and r.fit and r.fit.ok]
        if p.tau_source == "smallest" and src:
            src = [min(src, key=lambda r: abs(r.dI))]
    for r in src:
        r.tau_used = True
    S = summarize(results, p)
    S.tau_note = tau_note
    return results, S


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
    return fit_exp(t[m], v[m], p.n_exp, fixed), (t0, t1)


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
    taus = np.array([r.fit.tau for r in used if r.tau_used])
    S.n_tau = len(taus)
    if len(taus):
        S.tau = float(np.median(taus) if p.tau_agg == "median" else np.mean(taus))
        S.tau_sd = float(np.std(taus, ddof=1)) if len(taus) > 1 else np.nan
    S.cm = capacitance_pF(S.tau, S.rin)
    ok = [r for r in used if r.tau_used and np.isfinite(r.r0) and r.r0 > 0]
    if ok:
        agg = np.median if p.tau_agg == "median" else np.mean
        S.r0 = float(agg([r.r0 for r in ok]))
        S.cm_r0 = float(agg([capacitance_pF(r.fit.tau, r.r0) for r in ok]))
    return S


COPY_HEADER = ("File", "Rin_MOhm", "Cm_pF")


def copy_row(path: str, S: "Summary") -> str:
    """One tab-separated line (file name, Rin MOhm, Cm pF) that pastes into three spreadsheet cells.
    Empty cells for values that could not be computed."""
    import os

    def num(x, dec):
        return f"{x:.{dec}f}" if x is not None and np.isfinite(x) else ""
    return "\t".join([os.path.basename(path or ""), num(S.rin, 2), num(S.cm, 1)])
