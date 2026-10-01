"""Shared numerics. Units: time ms, Vm mV, Im pA, rates Hz."""
from typing import Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy import signal


def ts_ms(t) -> float:
    return float(t[1] - t[0])


def fs_hz(t) -> float:
    return 1000.0 / ts_ms(t)


def idx_at(t, ms: float) -> int:
    """Nearest sample index to a time (clipped to the record)."""
    return int(np.clip(round((ms - t[0]) / ts_ms(t)), 0, len(t) - 1))


def window_mean(t, y, a: float, b: float) -> float:
    i, j = idx_at(t, a), idx_at(t, b)
    if j <= i:
        return float(y[i])
    return float(np.mean(y[i:j]))


def interp_200khz(y, t, target_hz: float = 200000.0):
    """Linear interpolation to 200 kHz (manual: 'Interpolate to 200 kHz')."""
    y = np.asarray(y, float)
    t = np.asarray(t, float)
    factor = target_hz / fs_hz(t)
    if factor <= 1.0001 or len(y) < 2:
        return y, t
    n = int(round((len(y) - 1) * factor)) + 1
    tn = np.linspace(t[0], t[-1], n)
    return np.interp(tn, t, y), tn


def moving_average(x, n: int):
    n = max(1, int(n))
    if n == 1:
        return np.asarray(x, float)
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def bessel_lowpass(x, fs, cutoff_hz, order=8):
    """Zero-phase low-pass Bessel (order is the effective order, as in the manual's Filter tool)."""
    nyq = fs / 2.0
    if cutoff_hz >= nyq:
        return np.asarray(x, float)
    b, a = signal.bessel(max(1, order // 2), cutoff_hz / nyq, "low")
    return signal.filtfilt(b, a, x)


def robust_line_ransac(x, y, residual_threshold: Optional[float] = None) -> Tuple[float, float, np.ndarray]:
    """Deterministic RANSAC line fit (slope, intercept, inlier mask).

    scikit-learn's RANSACRegressor (used by Scala et al. for the rheobase) is random. Here every
    2-point model is evaluated (exhaustive RANSAC): the model with most inliers wins, ties broken by
    smallest inlier residual sum, and the line is then refit by least squares on its inliers. Default
    residual threshold = median absolute deviation of y (scikit-learn's default).
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    if n < 2:
        raise ValueError("need >= 2 points")
    if residual_threshold is None:
        residual_threshold = float(np.median(np.abs(y - np.median(y))))
        if residual_threshold == 0:
            residual_threshold = 1e-12
    best = None
    for i in range(n):
        for j in range(i + 1, n):
            if x[i] == x[j]:
                continue
            m = (y[j] - y[i]) / (x[j] - x[i])
            c = y[i] - m * x[i]
            res = np.abs(y - (m * x + c))
            inl = res <= residual_threshold
            key = (int(inl.sum()), -float(res[inl].sum()))
            if best is None or key > best[0]:
                best = (key, inl)
    if best is None:
        raise ValueError("degenerate x")
    inl = best[1]
    if inl.sum() >= 2 and np.ptp(x[inl]) > 0:
        m, c = np.polyfit(x[inl], y[inl], 1)
    else:
        m, c = np.polyfit(x, y, 1)
    return float(m), float(c), inl


def monoexp(x, b0, b1, tau):
    return b0 + b1 * np.exp(-x / tau)


def fit_monoexp(t, y, fixed_b0: Optional[float] = None):
    """b0 + b1*exp(-(t-t0)/tau) with scipy least_squares (TRF), tau > 0. Returns dict or None."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if len(t) < 5:
        return None
    x = t - t[0]
    n_end = max(1, len(y) // 10)
    b0s = float(np.mean(y[-n_end:])) if fixed_b0 is None else fixed_b0
    d0 = float(y[0] - b0s)
    half = b0s + 0.5 * d0
    cross = np.where((y - half) * np.sign(d0 if d0 else 1) <= 0)[0]
    thalf = x[cross[0]] if len(cross) else x[-1] / 5.0
    tau0 = max(thalf / np.log(2), 1e-3 * x[-1], 1e-3)
    try:
        if fixed_b0 is None:
            r = least_squares(lambda p: monoexp(x, *p) - y, [b0s, d0, tau0],
                              bounds=([-np.inf, -np.inf, 1e-6], [np.inf, np.inf, np.inf]), method="trf")
            b0, b1, tau = r.x
        else:
            r = least_squares(lambda p: monoexp(x, fixed_b0, *p) - y, [d0, tau0],
                              bounds=([-np.inf, 1e-6], [np.inf, np.inf]), method="trf")
            b0, (b1, tau) = fixed_b0, r.x
    except Exception:
        return None
    yf = monoexp(x, b0, b1, tau)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - float(np.sum((y - yf) ** 2)) / ss_tot if ss_tot > 0 else float("nan")
    return dict(b0=float(b0), b1=float(b1), tau=float(tau), r2=r2, t=t, y_fit=yf, ok=bool(r.success))


def half_width_and_times(y_rise, t_rise, y_fall, t_fall, half_level, interp=True):
    """Nearest-sample half-amplitude crossing on rise and fall (manual: FWHM, optional 200 kHz)."""
    if interp:
        y_rise, t_rise = interp_200khz(y_rise, t_rise)
        y_fall, t_fall = interp_200khz(y_fall, t_fall)
    i1 = int(np.abs(half_level - y_rise).argmin())
    i2 = int(np.abs(half_level - y_fall).argmin())
    return t_fall[i2] - t_rise[i1], t_rise[i1], t_fall[i2]


def rise_time(y, t, lo, hi, lo_pct=10.0, hi_pct=90.0, interp=True):
    """Time between lo_pct and hi_pct of (hi - lo), nearest samples. Returns (dur, t1, t2, v1, v2)."""
    if interp:
        y, t = interp_200khz(y, t)
    amp = hi - lo
    c1, c2 = lo + amp * lo_pct / 100.0, lo + amp * hi_pct / 100.0
    i1, i2 = int(np.abs(y - c1).argmin()), int(np.abs(y - c2).argmin())
    return t[i2] - t[i1], t[i1], t[i2], y[i1], y[i2]


def fall_time(y, t, hi, lo, hi_pct=10.0, lo_pct=90.0, interp=True):
    """Time from (hi_pct) to (lo_pct) of the way down from hi to lo. Returns (dur, t1, t2, v1, v2)."""
    if interp:
        y, t = interp_200khz(y, t)
    amp = hi - lo
    c1, c2 = hi - amp * hi_pct / 100.0, hi - amp * lo_pct / 100.0
    i1, i2 = int(np.abs(y - c1).argmin()), int(np.abs(y - c2).argmin())
    return t[i2] - t[i1], t[i1], t[i2], y[i1], y[i2]
