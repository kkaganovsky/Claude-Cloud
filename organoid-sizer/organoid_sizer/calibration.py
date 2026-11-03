"""Turning a known length into micrometres per pixel.

Ways to calibrate (all end in ``um_per_px``):

* **Known pixel size** typed in: µm/px, nm/px, px/µm, px/mm, or scanner DPI.
* **Line on a known object** (ruler, scale bar, well diameter): length in px from the drawn line, real length typed in.
* **Scale-bar auto-detect**: finds solid horizontal bars (typical figure / microscope scale bars) and returns
  their exact pixel length; the user only types the length printed beside it.
* **Ruler ticks**: intensity profile along a line drawn on a graduated ruler; the tick period is found by
  autocorrelation (sub-pixel), which averages over many ticks and is more precise than clicking two marks.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy import ndimage as ndi

from .imageio import calibration_dict, unit_to_um


def from_pixel_size(value: float, unit: str) -> dict:
    """unit: 'um/px', 'nm/px', 'mm/px', 'px/um', 'px/mm', 'px/cm', 'dpi'."""
    v = float(value)
    if v <= 0:
        raise ValueError("The value must be positive.")
    u = unit.strip().lower().replace("µ", "u")
    table = {
        "um/px": v, "nm/px": v / 1000, "mm/px": v * 1000,
        "px/um": 1 / v, "px/mm": 1000 / v, "px/cm": 10000 / v,
        "dpi": 25400 / v, "ppi": 25400 / v,
    }
    if u not in table:
        raise ValueError(f"Unknown unit {unit}")
    um = table[u]
    return calibration_dict(um, "entered pixel size", f"{v:g} {unit} = {um:.5g} µm/px", True)


def from_line(p0, p1, length: float, unit: str, what: str = "line on known object") -> dict:
    px = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    f = unit_to_um(unit)
    if not f:
        raise ValueError(f"Unknown unit {unit}")
    if px < 2:
        raise ValueError("The line is too short.")
    real_um = float(length) * f
    um = real_um / px
    return calibration_dict(um, what, f"{px:.2f} px = {length:g} {unit} -> {um:.5g} µm/px", True)


def from_period(period_px: float, spacing: float, unit: str, n_ticks: int | None = None) -> dict:
    f = unit_to_um(unit)
    um = float(spacing) * f / period_px
    extra = f" ({n_ticks} ticks)" if n_ticks else ""
    return calibration_dict(um, "ruler ticks", f"tick period {period_px:.3f} px = {spacing:g} {unit}{extra} "
                                              f"-> {um:.5g} µm/px", True)


def detect_scale_bars(gray: np.ndarray, max_candidates: int = 6) -> list[dict]:
    """Find solid, thin, horizontal (or vertical) bars, dark or bright. Returns candidates with endpoints in px."""
    h, w = gray.shape
    u8 = (np.clip(gray, 0, 1) * 255).astype(np.uint8)
    cands = []
    for polarity, img in (("bright", u8), ("dark", 255 - u8)):
        # a bar is much brighter than its surroundings: top-hat removes the background
        k = max(15, int(min(h, w) * 0.05)) | 1
        th = cv2.morphologyEx(img, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        thr = max(40, int(np.percentile(th, 99.5)))
        bw = (th >= thr * 0.6).astype(np.uint8)
        # also try a plain high threshold (bars on uniform black / white borders)
        bw2 = (img >= 230).astype(np.uint8)
        for b in (bw, bw2):
            n, cc, stats, _ = cv2.connectedComponentsWithStats(b, 8)
            for i in range(1, n):
                x, y, bwid, bht, area = stats[i]
                long_, short = max(bwid, bht), min(bwid, bht)
                if long_ < max(12, 0.02 * max(w, h)) or short < 1:
                    continue
                if long_ / short < 4 or short > 0.06 * min(w, h) + 4:
                    continue
                fill = area / float(bwid * bht)
                if fill < 0.85:
                    continue
                horizontal = bwid >= bht
                # sub-pixel length from the intensity profile across the bar ends
                if horizontal:
                    yc = y + bht / 2.0
                    p0, p1 = (float(x), yc), (float(x + bwid), yc)
                else:
                    xc = x + bwid / 2.0
                    p0, p1 = (xc, float(y)), (xc, float(y + bht))
                score = fill * min(1.0, long_ / (8 * short)) * (1.2 if horizontal else 1.0)
                cands.append({"p0": p0, "p1": p1, "length_px": float(long_), "thickness_px": float(short),
                              "orientation": "horizontal" if horizontal else "vertical",
                              "polarity": polarity, "score": float(score),
                              "bbox": [int(x), int(y), int(x + bwid), int(y + bht)]})
    # de-duplicate (same bar found in both binarisations)
    cands.sort(key=lambda c: -c["score"])
    out = []
    for c in cands:
        if any(_overlap(c["bbox"], o["bbox"]) > 0.5 for o in out):
            continue
        out.append(c)
        if len(out) >= max_candidates:
            break
    return out


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return inter / small


def ruler_period(gray: np.ndarray, p0, p1, band: int = 3) -> dict:
    """Tick period (px) along the line p0 -> p1 on a graduated ruler, from the autocorrelation of the profile."""
    x0, y0 = p0
    x1, y1 = p1
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 20:
        raise ValueError("Draw the line along at least a few ruler ticks.")
    n = int(L * 2)  # 0.5 px sampling
    t = np.linspace(0, 1, n)
    xs = x0 + (x1 - x0) * t
    ys = y0 + (y1 - y0) * t
    nx, ny = -(y1 - y0) / L, (x1 - x0) / L
    prof = np.zeros(n)
    for o in range(-band, band + 1):
        prof += ndi.map_coordinates(gray.astype(np.float64), [ys + o * ny, xs + o * nx], order=1, mode="nearest")
    prof /= 2 * band + 1
    # remove slow trend
    trend = ndi.gaussian_filter1d(prof, sigma=max(5, n / 20))
    p = prof - trend
    p -= p.mean()
    if np.allclose(p, 0):
        raise ValueError("No intensity variation along the line.")
    ac = np.correlate(p, p, mode="full")[n - 1:]
    ac /= ac[0]
    # first strong local maximum after the first zero crossing
    zc = np.argmax(ac < 0)
    if zc == 0:
        raise ValueError("No repeating ticks found along the line.")
    seg = ac[zc:]
    # first local maximum that is at least half as strong as the strongest (avoids locking onto 2x the period)
    top = seg.max()
    k = None
    for j in range(1, len(seg) - 1):
        if seg[j] >= seg[j - 1] and seg[j] >= seg[j + 1] and seg[j] >= 0.5 * top:
            k = j + zc
            break
    if k is None:
        k = int(np.argmax(seg)) + zc
    # parabolic refinement
    if 1 <= k < len(ac) - 1:
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b + c
        k = k + (0.5 * (a - c) / den if den != 0 else 0)
    # refine with multiples: the m-th peak / m averages error over many ticks
    period = k
    m_max = int((n - 1) // period)
    ests = [period]
    for m in range(2, min(m_max, 40) + 1):
        c = int(round(m * period))
        lo, hi = max(1, c - 3), min(len(ac) - 2, c + 3)
        if lo >= hi:
            break
        j = lo + int(np.argmax(ac[lo:hi + 1]))
        a, b, cc = ac[j - 1], ac[j], ac[j + 1]
        den = a - 2 * b + cc
        jj = j + (0.5 * (a - cc) / den if den != 0 else 0)
        if ac[j] > 0.2:
            ests.append(jj / m)
    period_samples = float(np.median(ests[-max(1, len(ests) // 2):]))
    period_px = period_samples * L / (n - 1)
    return {"period_px": period_px, "n_ticks": int(L / period_px), "strength": float(ac[int(round(k))]),
            "profile": prof[:: max(1, n // 400)].tolist()}
