"""sEPSC classifier signal code: preprocessing, candidate detection, windows, tabular features.

Must stay identical to the code the model weights were trained with (train and apply share these functions)."""
import numpy as np
from scipy import signal, ndimage

FS = 10000.0            # working rate (Hz)
PRE, POST = 100, 400    # samples before / after peak at 10 kHz (10 ms / 40 ms)
W = PRE + POST


def to_10k(y, fs):
    if abs(fs - FS) < 1:
        return np.asarray(y, float)
    from math import gcd
    a, b = int(FS), int(round(fs))
    g = gcd(a, b)
    return signal.resample_poly(np.asarray(y, float), a // g, b // g)


LPF_HZ = 3000.0
_SOS = signal.butter(4, LPF_HZ, "low", fs=FS, output="sos")


def prep_trace(y, fs):
    """Common preprocessing for training and deployment: 10 kHz resample + 4th-order zero-phase 3 kHz low-pass
    (aligns bandwidth across acquisition settings; new recordings carried 2-5 kHz noise absent from the training set)."""
    return signal.sosfiltfilt(_SOS, to_10k(y, fs))


def template(rise_ms=0.5, decay_ms=5.0, dur_ms=40.0):
    t = np.arange(int(dur_ms * FS / 1000)) / FS * 1000
    tp = (rise_ms * decay_ms / (decay_ms - rise_ms)) * np.log(decay_ms / rise_ms)
    h = -(np.exp(-t / decay_ms) - np.exp(-t / rise_ms))
    h = h / np.abs(h).max()
    return h


TPL = template()
_t0 = TPL - TPL.mean()
_t0 = _t0 / np.sqrt((_t0 ** 2).sum())


def noise_sd(y):
    d = np.diff(y)
    return 1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2)


def detect(y, thr=3.0, min_dist_ms=4.0):
    """Permissive matched-filter candidate detection on a 10 kHz trace (inward events). Returns peak indices and scores."""
    base = ndimage.uniform_filter1d(y, int(0.1 * FS), mode="nearest")
    z = y - base
    sd = noise_sd(z)
    if sd <= 0:
        return np.array([], int), np.array([]), sd
    L = len(_t0)
    full = signal.fftconvolve(z, _t0[::-1], mode="full")
    sc = full[L - 1:L - 1 + len(z)] / sd                      # score at n = template onset aligned to n
    pk, _ = signal.find_peaks(sc, height=thr, distance=int(min_dist_ms * FS / 1000))
    zs = ndimage.uniform_filter1d(z, 3, mode="nearest")
    out = []
    for p in pk:
        c = p + 14                                             # template minimum is ~1.4 ms after onset
        lo, hi = max(0, c - 25), min(len(z), c + 25)
        if hi - lo > 5:
            out.append(lo + int(np.argmin(zs[lo:hi])))
    out = np.unique(np.array(out, int))
    return out, sc[np.clip(out - 14, 0, len(sc) - 1)], sd


def window(y, i):
    """PRE+POST window around index i; NaN-padded (None if no room)."""
    lo, hi = i - PRE, i + POST
    if lo < 0 or hi > len(y):
        return None
    return y[lo:hi]


WPRE, WPOST = 300, 1200      # stored wide window (30 ms before / 120 ms after) for the window-size / multiscale study


def window_wide(y, i):
    lo, hi = i - WPRE, i + WPOST
    if lo < 0 or hi > len(y):
        return None
    return y[lo:hi]


def feats(w, sd_rec, dt_prev, dt_next, n_near):
    """Tabular features from one baseline-NOT-subtracted window (peak at sample PRE)."""
    b = np.mean(w[:60])
    x = w - b
    pre = w[:60]
    pre_sd = np.std(pre - np.polyval(np.polyfit(np.arange(60), pre, 1), np.arange(60)))
    slope = np.polyfit(np.arange(60), pre, 1)[0] * FS / 1000.0          # pA per ms
    a = -x[PRE - 8:PRE + 12].min()
    ia = PRE - 8 + int(np.argmin(x[PRE - 8:PRE + 12]))
    amp = max(a, 1e-6)
    # rise 10-90 on rising side
    l10, l90 = 0.1 * amp, 0.9 * amp
    k = ia
    while k > 0 and -x[k] > l90: k -= 1
    k90 = k
    while k > 0 and -x[k] > l10: k -= 1
    k10 = k
    rise = (k90 - k10) / FS * 1000
    # decay to 37% and half width
    k = ia
    while k < W - 1 and -x[k] > 0.37 * amp: k += 1
    dec37 = (k - ia) / FS * 1000
    k = ia
    while k < W - 1 and -x[k] > 0.5 * amp: k += 1
    khr = k
    k = ia
    while k > 0 and -x[k] > 0.5 * amp: k -= 1
    fwhm = (khr - k) / FS * 1000
    post = x[300:500]
    post_mean = post.mean() / amp
    area = -x[ia:ia + 300].sum() / FS * 1000 / amp
    pk_sharp = -(x[ia - 1] + x[min(ia + 1, W - 1)] - 2 * x[ia]) / amp if 1 <= ia < W - 1 else 0
    d = np.diff(x[PRE - 20:PRE + 40])
    maxslope_in = -d.min() / amp * FS / 1000 if d.size else 0
    maxslope_out = d.max() / amp * FS / 1000 if d.size else 0
    # template correlation over event segment
    seg = x[ia - 10:ia + 190] if ia >= 10 and ia + 190 <= W else x[PRE - 10:PRE + 190]
    tt = np.pad(TPL, (10, max(0, len(seg) - len(TPL) - 10)))[:len(seg)]
    cc = np.corrcoef(seg, tt)[0, 1] if np.std(seg) > 0 and np.std(tt) > 0 else 0.0
    sk = float(((x - x.mean()) ** 3).mean() / (x.std() ** 3 + 1e-9))
    return [amp, amp / (sd_rec + 1e-9), amp / (pre_sd + 1e-9), pre_sd / (sd_rec + 1e-9), slope / (amp + 1e-9), rise, dec37, fwhm,
            post_mean, area, pk_sharp, maxslope_in, maxslope_out, cc, sk, np.log10(amp + 1), np.log10(sd_rec + 1e-6),
            np.log10(dt_prev + 1), np.log10(dt_next + 1), n_near, (ia - PRE) / 10.0]


FEAT_NAMES = ["amp", "snr_rec", "snr_pre", "pre_noise_ratio", "pre_slope_rel", "rise_ms", "decay37_ms", "fwhm_ms",
              "post_rel", "area_rel", "peak_sharp", "max_slope_in", "max_slope_out", "tpl_corr", "skew", "log_amp",
              "log_noise", "log_dt_prev", "log_dt_next", "n_near", "peak_off_ms"]


def detect_slope(y, k=4.0, min_dist_ms=1.5):
    """Derivative-based candidates: fast inward onsets; peak = raw minimum within 4 ms after the steepest point."""
    ys = ndimage.uniform_filter1d(y, 3, mode="nearest")
    d = np.diff(ys)
    sd = 1.4826 * np.median(np.abs(d - np.median(d)))
    if sd <= 0:
        return np.array([], int)
    pk, _ = signal.find_peaks(-d, height=k * sd, distance=int(min_dist_ms * FS / 1000))
    out = []
    for p in pk:
        lo, hi = p, min(len(y), p + 45)
        if hi - lo > 3:
            q = lo + int(np.argmin(ys[lo:hi]))
            # require a real drop relative to the pre-onset baseline
            b0 = np.median(ys[max(0, p - 40):max(1, p - 5)]) if p > 6 else ys[0]
            if b0 - ys[q] > 2.0 * sd * 3:
                out.append(q)
    return np.unique(np.array(out, int))


def detect_all(y, thr=3.0, k=4.0):
    a, sc, sd = detect(y, thr=thr, min_dist_ms=2.5)
    b = detect_slope(y, k=k)
    allp = np.unique(np.concatenate([a, b])) if len(a) or len(b) else np.array([], int)
    # merge candidates closer than 2 ms: keep the deeper one
    if len(allp) > 1:
        keep = [allp[0]]
        for p in allp[1:]:
            if p - keep[-1] < 20:
                if y[p] < y[keep[-1]]:
                    keep[-1] = p
            else:
                keep.append(p)
        allp = np.array(keep, int)
    return allp, sd
