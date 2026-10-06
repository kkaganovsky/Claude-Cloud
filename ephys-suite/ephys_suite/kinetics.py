"""Action potential kinetics (Easy Electrophysiology manual section 10 and its open-source code, re-implemented).

Threshold: Vm at the first / third derivative maximum or cutoff, Sekerli et al. 2004 Method I / II, leading
inflection or maximum curvature (Appendix I), searched in `thr_search` ms before the peak. Derivatives are forward
differences of Vm in mV/ms.
Amplitude = peak - threshold. fAHP / mAHP = (minimum Vm in a window after the peak) - threshold.
Rise = 10-90 % of amplitude (threshold -> peak), decay = 10-90 % of the peak -> fAHP amplitude (or peak -> threshold
crossing), half-width = FWHM, all with optional linear 200 kHz interpolation (nearest-sample crossings).
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from .common import (fall_time, fs_hz, half_width_and_times, idx_at, interp_200khz, rise_time, ts_ms)

THRESHOLD_METHODS = ("first_deriv", "third_deriv", "method_I", "method_II", "leading_inflection", "max_curvature")


@dataclass
class KineticsParams:
    thr_method: str = "method_II"
    thr_search: float = 10.0             # ms before the peak (Easy Electrophysiology setting used in the lab)
    first_deriv_cutoff_mode: str = "max" # 'max' | 'cutoff' (first_deriv)
    first_deriv_cutoff: float = 20.0     # mV/ms
    third_deriv_cutoff_mode: str = "max"
    third_deriv_cutoff: float = 20.0
    method_I_lower: float = 1.0          # first-derivative lower bound (mV/ms)
    method_II_lower: float = 1.0
    fahp: Tuple[float, float] = (0.0, 5.0)      # ms after peak
    mahp: Tuple[float, float] = (10.0, 50.0)
    interp: bool = True                  # 200 kHz
    rise_pct: Tuple[float, float] = (10.0, 90.0)
    decay_pct: Tuple[float, float] = (10.0, 90.0)
    decay_to_thr: bool = False           # False: peak -> fAHP, True: peak -> sample nearest the threshold
    max_slope: bool = False
    rise_slope_samples: int = 4
    decay_slope_samples: int = 4


@dataclass
class APKinetics:
    ok: bool
    why: str = ""
    thr_idx: int = -1
    peak_idx: int = -1
    thr_t: float = np.nan
    thr_v: float = np.nan
    peak_t: float = np.nan
    peak_v: float = np.nan
    amplitude: float = np.nan
    rise_ms: float = np.nan
    decay_ms: float = np.nan
    half_width_ms: float = np.nan
    fahp: float = np.nan           # relative to threshold
    fahp_v: float = np.nan
    fahp_t: float = np.nan
    mahp: float = np.nan
    mahp_v: float = np.nan
    mahp_t: float = np.nan
    max_rise: float = np.nan       # mV/ms
    max_decay: float = np.nan
    marks: dict = field(default_factory=dict)   # sample times/values for plotting


def _derivs(v, start, peak):
    """Forward-difference derivatives over [start, peak+2) as in the reference implementation."""
    fd = np.diff(v) / 1.0                      # per-sample; scaled by dt by the caller
    return fd


def threshold_index(v, dt, start, peak, p: KineticsParams):
    """Index (absolute) of the AP threshold, or None."""
    # Single precision, as in Easy Electrophysiology. ABF voltages come in fixed ADC steps (~0.03 mV), so the
    # derivative-based scores (Method II especially) often tie exactly between two samples; which one wins then
    # depends only on rounding. Rounding the trace to float32 first reproduces EE's choice in every case checked.
    v = np.asarray(v, dtype=np.float32).astype(np.float64)
    fd = np.diff(v) / dt
    calc = fd[start:peak + 2]
    f1 = calc[:-2]
    if f1.size == 0:
        return None
    m = p.thr_method
    if m == "first_deriv":
        k = int(np.nanargmax(f1)) if p.first_deriv_cutoff_mode == "max" else int((f1 > p.first_deriv_cutoff).argmax())
    elif m == "leading_inflection":
        k = int(np.nanargmin(f1))
    else:
        s2f = np.diff(calc)
        s2 = s2f[:-1]
        if s2.size == 0:
            return None
        if m == "method_I":
            g = s2 / np.where(f1 == 0, np.nan, f1)
            g[f1 <= p.method_I_lower] = np.nan
            if np.all(np.isnan(g)):
                return None
            k = int(np.nanargmax(g))
        elif m == "max_curvature":
            k = int(np.nanargmax(s2 * (1 + f1 ** 2) ** (-1.5)))
        else:
            s3 = np.diff(s2f)
            if s3.size == 0:
                return None
            if m == "third_deriv":
                k = int(np.nanargmax(s3)) if p.third_deriv_cutoff_mode == "max" else int((s3 > p.third_deriv_cutoff).argmax())
            elif m == "method_II":
                den = f1 ** 3
                h = np.divide(s3 * f1 - s2 ** 2, den, out=np.zeros_like(den), where=den != 0)
                h[f1 <= p.method_II_lower] = 0
                if not np.any(h):
                    return None
                k = int(np.argmax(h))
            else:
                raise ValueError(m)
    return start + k


def max_slope(t, y, window_samples):
    """Maximum slope (mV/ms) as an OLS regression over `window_samples` consecutive samples (manual)."""
    n = len(y) - window_samples + 1
    if n < 1 or window_samples < 2:
        return np.nan, None
    if window_samples == 2:
        s = np.diff(y) / np.diff(t)
        return (float(s.max()), None) if s.size else (np.nan, None)
    best, bi = None, 0
    for i in range(n):
        sl = np.polyfit(t[i:i + window_samples], y[i:i + window_samples], 1)[0]
        if best is None or abs(sl) > abs(best):
            best, bi = sl, i
    return float(best), bi


def analyse_ap(t, v, peak_idx, p: KineticsParams) -> APKinetics:
    dt = ts_ms(t)
    fs = fs_hz(t)
    start = max(0, peak_idx - int(round(p.thr_search / dt)))
    if start <= 0 and peak_idx < 3:
        return APKinetics(False, "peak too close to record start")
    f_stop = p.fahp[1]
    m_stop = p.mahp[1]
    # clip AHP windows to the record end (manual / reference: shortened, rejected if emptied)
    rem = (len(t) - 1 - peak_idx) * dt
    f_stop, m_stop = min(f_stop, rem), min(m_stop, rem)
    if f_stop <= p.fahp[0] or m_stop <= p.mahp[0]:
        return APKinetics(False, "AHP window beyond record end")
    thr = threshold_index(v, dt, start, peak_idx, p)
    if thr is None:
        return APKinetics(False, "no threshold found")
    thr_v, peak_v = float(v[thr]), float(v[peak_idx])
    amp = peak_v - thr_v
    spms = fs / 1000.0

    def ahp(a, b):
        i0 = peak_idx + int(round(a * spms))
        i1 = peak_idx + int(round(b * spms))
        k = i0 + int(np.argmin(v[i0:i1 + 1]))
        return k, float(v[k])

    fi, fv = ahp(p.fahp[0], f_stop)
    mi, mv = ahp(p.mahp[0], m_stop)
    # peak -> end of decay
    p2f = slice(peak_idx, fi + 1)
    if p.decay_to_thr:
        end = peak_idx + int(np.abs(v[p2f] - thr_v).argmin())
        low_v = thr_v
    else:
        end, low_v = fi, fv
    if end - peak_idx < 2:
        return APKinetics(False, "decay segment too short")
    r = rise_time(v[thr:peak_idx + 1], t[thr:peak_idx + 1], thr_v, peak_v, *p.rise_pct, interp=p.interp)
    d = fall_time(v[peak_idx:end + 1], t[peak_idx:end + 1], peak_v, low_v, *p.decay_pct, interp=p.interp)
    hw, t_hr, t_hd = half_width_and_times(v[thr:peak_idx + 1], t[thr:peak_idx + 1], v[peak_idx:end + 1],
                                          t[peak_idx:end + 1], thr_v + amp / 2.0, p.interp)
    out = APKinetics(True, thr_idx=thr, peak_idx=peak_idx, thr_t=float(t[thr]), thr_v=thr_v, peak_t=float(t[peak_idx]),
                     peak_v=peak_v, amplitude=amp, rise_ms=float(r[0]), decay_ms=float(d[0]), half_width_ms=float(hw),
                     fahp=fv - thr_v, fahp_v=fv, fahp_t=float(t[fi]), mahp=mv - thr_v, mahp_v=mv, mahp_t=float(t[mi]))
    out.marks = dict(rise=(r[1], r[2], r[3], r[4]), decay=(d[1], d[2], d[3], d[4]),
                     half=(t_hr, t_hd, thr_v + amp / 2.0))
    if p.max_slope:
        out.max_rise = max_slope(t[thr:peak_idx + 1], v[thr:peak_idx + 1], p.rise_slope_samples)[0]
        out.max_decay = max_slope(t[peak_idx:end + 1], v[peak_idx:end + 1], p.decay_slope_samples)[0]
    return out


def phase_plot(t, v, peak_idx, window_ms=5.0, interpolate=False):
    """Phase-space plot: Vm (x) vs dVm/dt in mV/ms (y), manual 'Phase Plot Analysis'."""
    dt = ts_ms(t)
    i0 = max(0, peak_idx - int(round(window_ms / dt)))
    i1 = min(len(v) - 1, peak_idx + int(round(window_ms / dt)))
    seg = v[i0:i1 + 1]
    return seg[:-1], np.diff(seg) / dt
