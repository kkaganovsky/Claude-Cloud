"""Synaptic event detection and kinetics (Easy Electrophysiology manual v2.6.3, section 7; re-implemented).

Detection measures
  deconvolution        Pernia-Andrade et al. 2012: FFT(data) / FFT(template), Gaussian band-pass, threshold = n x sigma of a
                       Gaussian fitted to the all-points histogram of the deconvolution (default n = 3.5). The 'convolution
                       method' in the project brief refers to this.
  correlation          Clements & Bekkers 1997 sliding-window template fit; correlation coefficient r (default cutoff 0.4).
  detection_criterion  same sliding window; |scale / standard error| (default cutoff 4).
  threshold            local maxima of the (direction-signed) data above a lower threshold.
Template: b0 + b1 (1 - exp(-x/rise)) exp(-x/decay) (manual Appendix I 1.3), default rise 0.5 ms, decay 5 ms
(Jonas et al. 1993).
Kinetics: per-event baseline (steepest of the longest lines to the peak), foot refinement (20-80 % rise line intersecting
the baseline, Jonas et al. 1993), amplitude, 10-90 rise, decay % (37 %), mono-exponential decay tau, FWHM, AUC.
Numeric defaults not given in the manual are this suite's own (see DECISIONS in the README).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy import signal, stats
from scipy.optimize import curve_fit, least_squares

from .common import (fit_monoexp, fs_hz, half_width_and_times, idx_at, interp_200khz, moving_average, rise_time,
                     ts_ms)


_trapz = getattr(np, "trapezoid", None) or np.trapz


# ----------------------------------------------------------------------------------------------------- template
def biexp_event(x, b0, b1, rise, decay):
    return b0 + b1 * (1.0 - np.exp(-x / rise)) * np.exp(-x / decay)


@dataclass
class EventParams:
    direction: int = -1                       # -1 inward (negative) events, +1 outward
    method: str = "deconvolution"             # 'deconvolution' | 'correlation' | 'detection_criterion' | 'threshold'
    rise_ms: float = 0.5
    decay_ms: float = 5.0
    width_ms: float = 25.0                    # template window
    corr_cutoff: float = 0.40
    dc_cutoff: float = 4.0
    deconv_sd: float = 3.5
    deconv_low_hz: float = 1.0                # manual: options default 1 Hz (background text says 0.1 Hz)
    deconv_high_hz: float = 200.0
    # lower / upper thresholds (pA, data units)
    lower_mode: str = "rms"                   # 'rms' (default) | 'linear' | 'curve' | 'none'
    lower_value: float = -5.0                 # linear: absolute level (negative events must go below it)
    rms_multiple: float = 2.0                 # rms: record mean -/+ n x RMS of the data (omit periods excluded)
    curve_order: int = 3                      # curve: polynomial order; lower_value is the offset from the curve
    upper_value: float = 0.0                  # 0 = off; events beyond +/- this level are excluded
    amplitude_threshold: float = 5.0          # pA, |peak - baseline|
    min_distance_ms: float = 5.0              # 'local maximum period'
    average_peak_ms: float = 0.0              # 0 = off
    baseline_search_ms: float = 10.0
    average_baseline_ms: float = 1.0
    decay_search_ms: float = 30.0
    decay_endpoint: str = "first_baseline_cross"   # or 'entire_search_region'
    decay_pct: float = 37.0
    rise_pct: Tuple[float, float] = (10.0, 90.0)
    interp: bool = True                       # 200 kHz
    fit_decay: bool = True                    # mono-exponential tau on peak -> endpoint
    omit: Tuple[Tuple[float, float], ...] = ()    # (start, end) ms within each record (e.g. a test pulse)


@dataclass
class Event:
    record: int
    peak_idx: int
    peak_t: float            # ms within the record
    time: float              # ms on the recording clock
    peak_im: float
    bl_idx: int = -1
    bl_t: float = np.nan
    bl_im: float = np.nan
    end_idx: int = -1
    amplitude: float = np.nan
    rise_ms: float = np.nan
    decay_pct_ms: float = np.nan
    tau_ms: float = np.nan
    half_width_ms: float = np.nan
    auc: float = np.nan      # pA ms, baseline -> endpoint
    interval_ms: float = np.nan
    manual: bool = False            # added by hand
    fitted: bool = True             # kinetics measured (False: peak, baseline and amplitude only)
    baseline_manual: bool = False   # baseline level set by hand


@dataclass
class EventResults:
    events: List[Event]
    measure: List[np.ndarray]         # detection measure per record (None for threshold)
    threshold: Optional[float]        # cutoff applied to the measure
    params: EventParams
    total_ms: float
    deconv_info: Optional[dict] = None


def make_template(p: EventParams, ts: float, normalise=False, b1=None):
    W = max(3, int(round(p.width_ms / ts)))
    x = np.arange(W) * ts
    b1 = p.direction if b1 is None else b1
    tpl = biexp_event(x, 0.0, b1, p.rise_ms, p.decay_ms)
    if normalise:
        lo, hi = tpl.min(), tpl.max()
        tpl = (tpl - hi) / abs(lo - hi) if p.direction == -1 else (tpl - lo) / abs(hi - lo)
    return x, tpl


def fit_template_to_event(t, y, direction=-1, rise0=0.5, decay0=5.0):
    """Fit b0 + b1(1-e^-x/rise)e^-x/decay to a selected event (manual: Generate Template). Returns dict."""
    x = np.asarray(t, float) - t[0]
    y = np.asarray(y, float)
    b0 = float(y[0])
    pk = y[np.argmin(y) if direction < 0 else np.argmax(y)]
    p0 = [b0, (pk - b0) * 1.5, rise0, decay0]
    try:
        f, _ = curve_fit(biexp_event, x, y, p0=p0, bounds=([-np.inf, -np.inf, 1e-3, 1e-2], [np.inf, np.inf, np.inf, np.inf]),
                         maxfev=20000)
    except Exception:
        return None
    yf = biexp_event(x, *f)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return dict(b0=f[0], b1=f[1], rise=f[2], decay=f[3], r2=1 - float(np.sum((y - yf) ** 2)) / ss_tot if ss_tot else np.nan,
                y_fit=yf)


# ------------------------------------------------------------------------------------- detection measures
def sliding_window_fit(data, template):
    """Clements & Bekkers 1997 sliding-window template fit. Returns (detection_criterion, scale, offset, r)."""
    n = template.size
    d = np.concatenate([data, np.full(n - 1, data[-1])])
    S1 = np.concatenate([[0.0], np.cumsum(d)])
    S2 = np.concatenate([[0.0], np.cumsum(d ** 2)])
    sum_d = S1[n:] - S1[:-n]
    sum_d2 = S2[n:] - S2[:-n]
    sum_td = signal.correlate(d, template, mode="valid")
    st, st2 = template.sum(), (template ** 2).sum()
    scale = (sum_td - st * sum_d / n) / (st2 - st * st / n)
    offset = (sum_d - scale * st) / n
    sse = (sum_d2 + scale ** 2 * st2 + n * offset ** 2
           - 2 * (scale * sum_td + offset * sum_d - scale * offset * st))
    err = sse / (n - 1)
    err[err < 0] = np.finfo(float).eps
    se = np.sqrt(err)
    dc = scale / se
    sst = sum_d2 - sum_d ** 2 / n
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = 1 - sse / sst
    r2[~np.isfinite(r2) | (r2 < 0)] = 0
    return dc, scale, offset, np.sqrt(r2)


def deconvolution(data, template, fs, low_hz, high_hz):
    """Pernia-Andrade et al. 2012: Fourier-domain deconvolution of data by template with a Gaussian band-pass."""
    n = data.shape[0]
    pad = np.zeros(n)
    pad[:template.shape[0]] = template
    fd = np.fft.fft(data)
    ft = np.fft.fft(pad)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = fd / ft
    dec[~np.isfinite(dec)] = 0
    freqs = np.fft.fftfreq(n, 1.0 / fs)
    g = 1.0 / np.sqrt(2 * np.pi * high_hz / fs) * np.exp(-0.5 * (freqs / high_hz) ** 2)
    g[np.abs(freqs) < low_hz] = 0
    return np.real(np.fft.ifft(g * dec * fs))


def _gauss(x, a, mu, sigma):
    return a * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2))


def deconvolution_threshold(measures: Sequence[np.ndarray], n_sd: float):
    """All-points histogram (sqrt(N) bins, 10x linear upsampling) of the deconvolution pooled over records, Gaussian fit;
    cutoff = n_sd x sigma. Falls back to the sample SD when the fit fails."""
    allv = np.concatenate([m[~np.isnan(m)] for m in measures])
    nb = max(10, int(np.sqrt(allv.size)))
    h, edges = np.histogram(allv, bins=nb)
    ctr = edges[1:]
    xi = np.linspace(ctr[0], ctr[-1], 10 * len(ctr))
    hi = np.interp(xi, ctr, h)
    try:
        popt, _ = curve_fit(_gauss, xi, hi, p0=[hi.max(), xi[np.argmax(hi)], np.std(allv)], maxfev=20000)
        mu, sigma = float(popt[1]), abs(float(popt[2]))
        ok = True
    except Exception:
        mu, sigma, ok = float(np.mean(allv)), float(np.std(allv)), False
    return n_sd * sigma, dict(mu=mu, sigma=sigma, edges=xi, freq=hi, gaussian_ok=ok)


# ------------------------------------------------------------------------------------------------ helpers
def _runs(mask):
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1


def _above_threshold_peaks(measure, data, thr, direction, smooth=None):
    """Contiguous regions of measure > thr (optionally bridged by a box of `smooth` samples), peak of `data` (argmax for
    direction>0, argmin otherwise) inside each."""
    jj = (measure > thr).astype(int)
    if smooth:
        box = np.concatenate([[0, 0], np.ones(smooth), [0, 0]])
        jj = np.convolve(jj, box)[:len(measure)]
        jj[jj > 0] = 1
    ups, downs = _runs(jj.astype(bool))
    pk = []
    for a, b in zip(ups, downs):
        seg = data[a:b + 1]
        pk.append(a + (int(np.argmax(seg)) if direction > 0 else int(np.argmin(seg))))
    return np.array(pk, dtype=int)


def _enforce_min_distance(peaks, heights, min_samples):
    """Keep the largest peaks first, drop any within min_samples of a kept one (scipy find_peaks `distance` logic)."""
    if len(peaks) == 0 or min_samples <= 1:
        return np.asarray(peaks, dtype=int)
    order = np.argsort(heights)[::-1]
    keep = np.ones(len(peaks), bool)
    for i in order:
        if not keep[i]:
            continue
        j = i - 1
        while j >= 0 and peaks[i] - peaks[j] < min_samples:
            keep[j] = False
            j -= 1
        j = i + 1
        while j < len(peaks) and peaks[j] - peaks[i] < min_samples:
            keep[j] = False
            j += 1
    return np.asarray(peaks)[keep]


def _omit_mask(t, p: EventParams):
    """True where the record is NOT inside an omit period."""
    keep = np.ones(len(t), bool)
    for a, b in p.omit:
        keep &= ~((t >= a) & (t <= b))
    return keep


def _lower_threshold_line(t, y, p: EventParams):
    """Level (array over samples, or None) that an event peak must pass (direction-aware)."""
    d = p.direction
    keep = _omit_mask(t, p)
    if p.lower_mode == "none":
        return None
    if p.lower_mode == "linear":
        return np.full(len(y), p.lower_value)
    if p.lower_mode == "rms":
        base = y[keep].mean() if keep.any() else y.mean()
        yy = y[keep] if keep.any() else y
        rms = np.sqrt(np.mean((yy - base) ** 2))
        return np.full(len(y), base + d * p.rms_multiple * rms)
    if p.lower_mode == "curve":
        c = np.polyfit(t[keep], y[keep], p.curve_order)
        return np.polyval(c, t) + p.lower_value
    raise ValueError(p.lower_mode)


def _smooth_peak(y, idx, p, ts):
    n = max(1, int(round(p.average_peak_ms / ts)))
    if n <= 1:
        return idx
    half = int(round(p.width_ms / ts / 2))
    a, b = max(0, idx - half), min(len(y), idx + half + 1)
    sm = moving_average(y[a:b], n)
    k = 3 * n
    c = idx - a
    lo, hi = max(0, c - k), min(len(sm), c + k + 1)
    j = lo + (int(np.argmax(sm[lo:hi])) if p.direction > 0 else int(np.argmin(sm[lo:hi])))
    return a + j


def _auto_baseline(t, y, peak_idx, start_idx, direction):
    """Steepest of the longest straight lines from samples in [start, peak) to the peak (manual: automatic baseline)."""
    idx = np.arange(max(0, start_idx), peak_idx)
    if idx.size < 2:
        return None
    dt = t[peak_idx] - t[idx]
    dy = y[peak_idx] - y[idx]
    norms = np.sqrt(dy ** 2 + dt ** 2)
    if not np.any(norms):
        return None
    slopes = dy / dt
    slopes[norms < np.percentile(norms, 60)] = np.nan
    if np.all(np.isnan(slopes)):
        return None
    k = int(np.nanargmax(slopes) if direction > 0 else np.nanargmin(slopes))
    return int(idx[k])


def _foot(t, y, bl_idx, peak_idx, bl_im):
    """Jonas et al. 1993: intersect the straight line through the 20-80 % rise points with the baseline."""
    seg_y, seg_t = y[bl_idx:peak_idx + 1], t[bl_idx:peak_idx + 1]
    if len(seg_y) < 3:
        return bl_idx
    amp = y[peak_idx] - bl_im
    i1 = int(np.abs(seg_y - (bl_im + 0.2 * amp)).argmin())
    i2 = int(np.abs(seg_y - (bl_im + 0.8 * amp)).argmin())
    if i2 == i1:
        return bl_idx
    slope = (seg_y[i2] - seg_y[i1]) / (seg_t[i2] - seg_t[i1])
    if slope == 0:
        return bl_idx
    foot_t = seg_t[i2] - abs(seg_y[i2] - bl_im) / abs(slope)
    # nearest data point (euclid, time and value scaled by their ranges) to (foot_t, bl_im)
    st = max(np.ptp(seg_t), 1e-9)
    sy = max(np.ptp(seg_y), 1e-9)
    d = np.sqrt(((seg_t - foot_t) / st) ** 2 + ((seg_y - bl_im) / sy) ** 2)
    return bl_idx + int(np.argmin(d))


# ------------------------------------------------------------------------------------------------ analysis
REASONS = {
    "ok": "added",
    "no_baseline": "no usable baseline before the peak",
    "bad_baseline": "baseline must be before the peak",
    "wrong_direction": "peak is not in the event direction relative to the baseline",
    "amplitude_threshold": "amplitude is below the amplitude threshold",
    "lower_threshold": "peak does not pass the lower (RMS / linear / curve) threshold",
    "upper_threshold": "peak exceeds the upper threshold",
    "duplicate": "an event is already there",
}


def analyse_event_ex(t, y, peak_idx, prev_peak_idx, next_peak_idx, p: EventParams, record=0, time_offset=0.0, line=None,
                     baseline=None, fit_kinetics=True, apply_thresholds=True, manual=False):
    """Measure one event. Returns (Event | None, reason key from REASONS).

    baseline: optional (index, level) set by hand; otherwise found automatically (steepest long line to the peak, foot
    refinement, short average). fit_kinetics=False measures only peak, baseline and amplitude. apply_thresholds=False
    skips the amplitude / lower / upper threshold tests (used for events added by hand with fitting off)."""
    ts = ts_ms(t)
    direction = p.direction
    pk_im = float(y[peak_idx])
    manual_bl = baseline is not None
    if manual_bl:
        bl_idx, bl_im = int(baseline[0]), float(baseline[1])
        if bl_idx >= peak_idx:
            return None, "bad_baseline"
    else:
        n_bl = int(round(p.baseline_search_ms / ts))
        start = peak_idx - n_bl
        if prev_peak_idx is not None:
            start = max(start, prev_peak_idx + 1)
        start = max(start, 0)
        bl_idx = _auto_baseline(t, y, peak_idx, start, direction)
        if bl_idx is None:
            return None, "no_baseline"
        bl_im = float(y[bl_idx])
        bl_idx = _foot(t, y, bl_idx, peak_idx, bl_im)
        n_avg = int(round(p.average_baseline_ms / ts))
        if n_avg > 0:
            bl_im = float(np.mean(y[max(0, bl_idx - n_avg):bl_idx + 1]))
    amp = pk_im - bl_im
    if np.sign(amp) != direction:
        return None, ("wrong_direction" if manual_bl else "no_baseline")
    if apply_thresholds:
        if abs(amp) < p.amplitude_threshold:
            return None, "amplitude_threshold"
        if line is not None:
            lvl = line[peak_idx]
            if (direction < 0 and not pk_im < lvl) or (direction > 0 and not pk_im > lvl):
                return None, "lower_threshold"
        if p.upper_value and abs(pk_im) > abs(p.upper_value):
            return None, "upper_threshold"
    ev = Event(record, int(peak_idx), float(t[peak_idx]), float(t[peak_idx] + time_offset), pk_im, int(bl_idx),
               float(t[bl_idx]), bl_im, amplitude=amp, manual=manual, fitted=bool(fit_kinetics), baseline_manual=manual_bl)
    if not fit_kinetics:
        return ev, "ok"
    n_dec = int(round(p.decay_search_ms / ts))
    stop = min(len(y) - 1, peak_idx + n_dec)
    if next_peak_idx is not None:
        stop = min(stop, next_peak_idx - 1)
    stop = min(max(stop, peak_idx + 2), len(y) - 1)
    if p.decay_endpoint == "first_baseline_cross":
        sm = moving_average(y[peak_idx:stop + 1], 3)
        cross = np.flatnonzero(sm >= bl_im) if direction < 0 else np.flatnonzero(sm <= bl_im)
        end = peak_idx + (int(cross[0]) if cross.size else int(np.abs(sm - bl_im).argmin()))
    else:
        end = stop
    ev.end_idx = int(min(max(end, peak_idx + 2), len(y) - 1))
    r = rise_time(y[bl_idx:peak_idx + 1], t[bl_idx:peak_idx + 1], bl_im, pk_im, *p.rise_pct, interp=p.interp)
    ev.rise_ms = float(r[0])
    seg_y = moving_average(y[peak_idx:ev.end_idx + 1], 3)
    seg_t = t[peak_idx:ev.end_idx + 1]
    sy, st = (interp_200khz(seg_y, seg_t) if p.interp else (seg_y, seg_t))
    level = bl_im + amp * p.decay_pct / 100.0
    ev.decay_pct_ms = float(st[int(np.abs(sy - level).argmin())] - t[peak_idx])
    if p.fit_decay and ev.end_idx - peak_idx >= 5:
        f = fit_monoexp(t[peak_idx:ev.end_idx + 1], y[peak_idx:ev.end_idx + 1])
        if f and f["ok"]:
            ev.tau_ms = f["tau"]
    hw, _, _ = half_width_and_times(y[bl_idx:peak_idx + 1], t[bl_idx:peak_idx + 1], y[peak_idx:ev.end_idx + 1],
                                    t[peak_idx:ev.end_idx + 1], bl_im + amp / 2.0, p.interp)
    ev.half_width_ms = float(hw)
    ev.auc = float(_trapz(y[bl_idx:ev.end_idx + 1] - bl_im, t[bl_idx:ev.end_idx + 1]))
    return ev, "ok"


def analyse_event(t, y, peak_idx, prev_peak_idx, next_peak_idx, p: EventParams, record=0, time_offset=0.0,
                  line=None) -> Optional[Event]:
    return analyse_event_ex(t, y, peak_idx, prev_peak_idx, next_peak_idx, p, record, time_offset, line)[0]


def detect_in_record(t, y, p: EventParams, measure=None, thr=None):
    """Putative event peak indices for one record."""
    ts = ts_ms(t)
    d = p.direction
    min_samp = max(1, int(round(p.min_distance_ms / ts)))
    if p.method == "threshold":
        pk = signal.find_peaks(y * d, distance=min_samp)[0]
        return np.asarray(pk, dtype=int)
    if p.method == "deconvolution":
        pk = _above_threshold_peaks(measure, measure, thr, +1)       # deconvolution peaks (positive)
        _, tpl = make_template(p, ts, normalise=True)
        bl_to_peak = int(np.argmin(tpl) if d < 0 else np.argmax(tpl))
        region = max(1, bl_to_peak) * 3                                # 3 x baseline-to-peak samples
        out = []
        for k in pk:
            if k + region > len(y):
                continue
            seg = y[k:k + region]
            out.append(k + (int(np.argmin(seg)) if d < 0 else int(np.argmax(seg))))
        return np.unique(np.asarray(out, dtype=int))
    # correlation / detection criterion
    W = int(round(p.width_ms / ts))
    return _above_threshold_peaks(measure, y, thr, d, smooth=int(W * 0.5))


def compute_measure(t, y, p: EventParams):
    ts = ts_ms(t)
    if p.method == "threshold":
        return None
    if p.method == "deconvolution":
        _, tpl = make_template(p, ts, normalise=True)
        rng = abs(float(y.min() - y.max())) if p.direction < 0 else abs(float(y.max() - y.min()))
        return deconvolution(y, tpl * rng, fs_hz(t), p.deconv_low_hz, p.deconv_high_hz)
    _, tpl = make_template(p, ts, b1=1.0)
    dc, scale, offset, r = sliding_window_fit(y, tpl)
    coef = r if p.method == "correlation" else np.abs(dc)
    wrong = scale < 0 if p.direction > 0 else scale > 0
    coef = coef.copy()
    coef[wrong] = 0
    return coef


def analyse_events(t, Y, p: EventParams, sweep_start_ms=None, sweeps=None) -> EventResults:
    """Detect events and measure kinetics in every record. Y: (n_records, N) in pA."""
    Y = np.atleast_2d(np.asarray(Y, float))
    n = Y.shape[0]
    sweeps = list(range(n)) if sweeps is None else list(sweeps)
    starts = np.zeros(n) if sweep_start_ms is None else np.asarray(sweep_start_ms, float)
    measures = [None] * n
    thr = None
    info = None
    if p.method != "threshold":
        for s in sweeps:
            measures[s] = compute_measure(t, Y[s], p)
        if p.method == "deconvolution":
            thr, info = deconvolution_threshold([measures[s] for s in sweeps], p.deconv_sd)
        elif p.method == "correlation":
            thr = p.corr_cutoff
        else:
            thr = p.dc_cutoff
    ts = ts_ms(t)
    events: List[Event] = []
    for s in sweeps:
        y = Y[s]
        pk = detect_in_record(t, y, p, measures[s], thr)
        min_samp = max(1, int(round(p.min_distance_ms / ts)))
        pk = np.unique(pk)
        pk = _enforce_min_distance(pk, y[pk] * p.direction if len(pk) else [], min_samp)
        pk = np.sort(pk)
        pk = np.array([_smooth_peak(y, k, p, ts) for k in pk], dtype=int) if p.average_peak_ms else pk
        line = _lower_threshold_line(t, y, p)
        for i, k in enumerate(pk):
            if any(a <= t[k] <= b for a, b in p.omit):      # omit periods apply to every record's own time axis
                continue
            prev_k = int(pk[i - 1]) if i > 0 else None
            next_k = int(pk[i + 1]) if i + 1 < len(pk) else None
            ev = analyse_event(t, y, int(k), prev_k, next_k, p, s, starts[s], line)
            if ev is not None:
                events.append(ev)
    total = float(sum(len(t) * ts for _ in sweeps))
    res = EventResults(events, measures, thr, p, total, info)
    _finish(res)
    return res


def _finish(res: EventResults):
    res.events.sort(key=lambda e: (e.time, e.peak_idx))
    prev = None
    for e in res.events:
        e.interval_ms = e.time - prev.time if prev is not None else np.nan
        prev = e


# ------------------------------------------------------------------------------------------------ outputs
def summarize(res: EventResults):
    ev = res.events
    if not ev:
        return dict(n=0, frequency_hz=0.0)
    f = lambda a: np.array([getattr(e, a) for e in ev], float)
    out = dict(n=len(ev), frequency_hz=len(ev) / (res.total_ms / 1000.0))
    for k, name in (("amplitude", "amplitude"), ("rise_ms", "rise_ms"), ("decay_pct_ms", "decay_pct_ms"), ("tau_ms", "tau_ms"),
                    ("half_width_ms", "half_width_ms"), ("auc", "auc"), ("interval_ms", "interval_ms")):
        v = f(name)
        v = v[~np.isnan(v)]
        out[k + "_mean"] = float(v.mean()) if v.size else np.nan
        out[k + "_median"] = float(np.median(v)) if v.size else np.nan
        out[k + "_sd"] = float(v.std(ddof=1)) if v.size > 1 else np.nan
    return out


def average_event(t, Y, events: Sequence[Event], pre_ms=5.0, post_ms=30.0, align="rise_half", p: EventParams = None):
    """Overlay of events aligned by the half-rise sample, peak or baseline (manual: Events Overlay / Average Event).
    Returns (time axis ms relative to the alignment point, matrix events x samples, mean)."""
    ts = ts_ms(t)
    npre, npost = int(round(pre_ms / ts)), int(round(post_ms / ts))
    rows = []
    for e in events:
        y = Y[e.record]
        if align == "peak":
            k = e.peak_idx
        elif align == "baseline":
            k = e.bl_idx
        else:
            half = e.bl_im + e.amplitude / 2.0
            seg = y[e.bl_idx:e.peak_idx + 1]
            k = e.bl_idx + int(np.abs(seg - half).argmin())
        if k - npre < 0 or k + npost >= len(y):
            continue
        rows.append(y[k - npre:k + npost + 1])
    if not rows:
        return np.array([]), np.empty((0, 0)), np.array([])
    M = np.vstack(rows)
    x = (np.arange(-npre, npost + 1)) * ts
    return x, M, M.mean(axis=0)


def cumulative_probability(values):
    v = np.sort(np.asarray(values, float))
    return v, (np.arange(1, len(v) + 1) / len(v)) if len(v) else np.array([])


def histogram(values, bins="auto"):
    return np.histogram(np.asarray(values, float), bins=bins)


def ks_test(a, b):
    """Two-sample Kolmogorov-Smirnov test (manual section 12)."""
    r = stats.ks_2samp(a, b)
    return float(r.statistic), float(r.pvalue)


# ------------------------------------------------------------------------------------------- manual editing
@dataclass
class ManualEvent:
    record: int
    peak_idx: int
    baseline: Optional[Tuple[int, float]] = None      # (sample, level) set by hand
    fit: bool = True


@dataclass
class EditLog:
    added: List[ManualEvent] = field(default_factory=list)
    deleted: List[Tuple[int, int]] = field(default_factory=list)                  # (record, peak_idx)
    baselines: dict = field(default_factory=dict)                                 # (record, peak_idx) -> (idx, level)

    def copy(self):
        return EditLog([ManualEvent(m.record, m.peak_idx, m.baseline, m.fit) for m in self.added], list(self.deleted),
                       dict(self.baselines))

    def is_empty(self):
        return not (self.added or self.deleted or self.baselines)


class EventSession:
    """Automatic detection plus manual edits (add / delete / move baseline) with undo.

    The visible event list is always rebuilt as: automatic events - deleted + baseline overrides + manual additions, so
    edits survive re-running detection with new parameters (`detect(keep_edits=True)`).
    Adding with fit=True applies the normal thresholds (like Easy Electrophysiology); with fit=False the amplitude / lower /
    upper thresholds are ignored and only peak, baseline and amplitude are measured."""

    def __init__(self, t, Y, params: EventParams, sweep_start_ms=None):
        self.t = np.asarray(t, float)
        self.Y = np.atleast_2d(np.asarray(Y, float))
        self.p = params
        self.starts = np.zeros(self.Y.shape[0]) if sweep_start_ms is None else np.asarray(sweep_start_ms, float)
        self.auto: Optional[EventResults] = None
        self.log = EditLog()
        self.events: List[Event] = []
        self._undo: List[EditLog] = []
        self._redo: List[EditLog] = []

    # ---------------------------------------------------------------------------------------------- detection
    @property
    def ts(self):
        return ts_ms(self.t)

    def detect(self, keep_edits=True):
        self.auto = analyse_events(self.t, self.Y, self.p, self.starts)
        if not keep_edits:
            self.log = EditLog()
            self._undo.clear()
            self._redo.clear()
        self._rebuild()
        return self.auto

    def _tol(self):
        return max(1, int(round(self.p.min_distance_ms / self.ts)))

    def _lines(self):
        return {r: _lower_threshold_line(self.t, self.Y[r], self.p) for r in range(self.Y.shape[0])}

    def _rebuild(self):
        if self.auto is None:
            self.events = []
            return
        tol = self._tol()
        dele = self.log.deleted
        keep = [e for e in self.auto.events
                if not any(e.record == r and abs(e.peak_idx - k) <= tol for r, k in dele)]
        out = []
        for e in keep:
            ov = self.log.baselines.get((e.record, e.peak_idx))
            if ov is not None:
                ne, why = analyse_event_ex(self.t, self.Y[e.record], e.peak_idx, None, None, self.p, e.record,
                                           self.starts[e.record], None, baseline=ov, apply_thresholds=False)
                e = ne if ne is not None else e
            out.append(e)
        for m in self.log.added:
            out = [e for e in out if not (e.record == m.record and abs(e.peak_idx - m.peak_idx) <= tol)]
            nb = self._neighbours(out, m.record, m.peak_idx)
            ov = m.baseline or self.log.baselines.get((m.record, m.peak_idx))
            ev, why = analyse_event_ex(self.t, self.Y[m.record], m.peak_idx, nb[0], nb[1], self.p, m.record,
                                       self.starts[m.record], None, baseline=ov, fit_kinetics=m.fit,
                                       apply_thresholds=False, manual=True)
            if ev is not None:
                out.append(ev)
        res = EventResults(out, self.auto.measure, self.auto.threshold, self.p, self.auto.total_ms, self.auto.deconv_info)
        _finish(res)
        self.events = res.events

    @staticmethod
    def _neighbours(events, record, peak_idx):
        prv = [e.peak_idx for e in events if e.record == record and e.peak_idx < peak_idx]
        nxt = [e.peak_idx for e in events if e.record == record and e.peak_idx > peak_idx]
        return (max(prv) if prv else None, min(nxt) if nxt else None)

    # ------------------------------------------------------------------------------------------------ helpers
    def results(self) -> EventResults:
        res = EventResults(list(self.events), self.auto.measure if self.auto else [], self.auto.threshold if self.auto else None,
                           self.p, self.auto.total_ms if self.auto else 0.0, self.auto.deconv_info if self.auto else None)
        return res

    def snap_peak(self, record, t0_ms, t1_ms=None, half_ms=2.0):
        """Peak sample in the event direction: the extreme of the data in [t0, t1] (drag), or +/- half_ms around t0 (click)."""
        y = self.Y[record]
        if t1_ms is None:
            a, b = t0_ms - half_ms, t0_ms + half_ms
        else:
            a, b = min(t0_ms, t1_ms), max(t0_ms, t1_ms)
        i, j = idx_at(self.t, a), idx_at(self.t, b)
        if j <= i:
            return i
        seg = y[i:j + 1]
        return i + (int(np.argmax(seg)) if self.p.direction > 0 else int(np.argmin(seg)))

    def find_event(self, record, peak_idx, tol_samples=None):
        tol = self._tol() if tol_samples is None else tol_samples
        c = [e for e in self.events if e.record == record and abs(e.peak_idx - peak_idx) <= tol]
        return min(c, key=lambda e: abs(e.peak_idx - peak_idx)) if c else None

    # ------------------------------------------------------------------------------------------------- edits
    def _checkpoint(self):
        self._undo.append(self.log.copy())
        self._redo.clear()

    def add(self, record, peak_idx, fit=True, baseline=None):
        """Add an event at a (snapped) peak sample. Returns (Event | None, reason). Nothing changes on failure."""
        if self.find_event(record, peak_idx) is not None:
            return self.find_event(record, peak_idx), "duplicate"
        nb = self._neighbours(self.events, record, peak_idx)
        # trial analysis, only to decide whether the add is allowed (thresholds apply when fitting kinetics)
        line = _lower_threshold_line(self.t, self.Y[record], self.p) if fit else None
        ev, why = analyse_event_ex(self.t, self.Y[record], peak_idx, nb[0], nb[1], self.p, record, self.starts[record], line,
                                   baseline=baseline, fit_kinetics=fit, apply_thresholds=fit, manual=True)
        if ev is None:
            return None, why
        self._checkpoint()
        self.log.deleted = [(r, k) for r, k in self.log.deleted if not (r == record and abs(k - peak_idx) <= self._tol())]
        self.log.added.append(ManualEvent(record, int(peak_idx), baseline, fit))
        self._rebuild()
        return self.find_event(record, peak_idx), "ok"

    def delete(self, ev: Event):
        self._checkpoint()
        key = (ev.record, ev.peak_idx)
        was_manual = [m for m in self.log.added if m.record == ev.record and abs(m.peak_idx - ev.peak_idx) <= self._tol()]
        if was_manual:
            self.log.added = [m for m in self.log.added if m not in was_manual]
        if not was_manual or any(e.record == ev.record and abs(e.peak_idx - ev.peak_idx) <= self._tol()
                                 for e in (self.auto.events if self.auto else [])):
            self.log.deleted.append(key)
        self.log.baselines.pop(key, None)
        self._rebuild()

    def set_baseline(self, ev: Event, bl_idx: int, bl_level: float):
        """Move an event's baseline by hand; its other parameters are re-measured."""
        if bl_idx >= ev.peak_idx:
            return None, "bad_baseline"
        trial, why = analyse_event_ex(self.t, self.Y[ev.record], ev.peak_idx, None, None, self.p, ev.record,
                                      self.starts[ev.record], None, baseline=(bl_idx, bl_level), fit_kinetics=ev.fitted,
                                      apply_thresholds=False, manual=ev.manual)
        if trial is None:
            return None, why
        self._checkpoint()
        m = [m for m in self.log.added if m.record == ev.record and abs(m.peak_idx - ev.peak_idx) <= self._tol()]
        if m:
            m[0].baseline = (int(bl_idx), float(bl_level))
        else:
            self.log.baselines[(ev.record, ev.peak_idx)] = (int(bl_idx), float(bl_level))
        self._rebuild()
        return self.find_event(ev.record, ev.peak_idx), "ok"

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self.log.copy())
        self.log = self._undo.pop()
        self._rebuild()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self.log.copy())
        self.log = self._redo.pop()
        self._rebuild()
        return True
