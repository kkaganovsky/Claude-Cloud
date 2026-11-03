"""Automatic detection: Cellpose (any version 2 / 3 / 4) or a classical threshold method (no AI, no GPU).

Both return an int32 label image (0 = background, 1..N = organoids) with holes filled, plus a log of what was done.
"""
from __future__ import annotations

import inspect
import threading

import cv2
import numpy as np
from scipy import ndimage as ndi

DEFAULTS = {
    "method": "cellpose",          # "cellpose" | "threshold"
    "model": "auto",               # auto -> cpsam (Cellpose 4) / cyto3 (3) / cyto2 (2); or a path to a custom model
    "diameter_um": None,           # expected organoid diameter in µm (used when the image is calibrated)
    "diameter_px": None,           # expected diameter in pixels (0 / None = let Cellpose estimate)
    "flow_threshold": 0.4,
    "cellprob_threshold": 0.0,
    "invert": False,               # invert grey levels before detection (dark organoids on a light background)
    "max_side": 2048,              # images larger than this are downscaled for detection (masks are scaled back)
    "use_gpu": True,
    "polarity": "auto",            # threshold method: "auto" | "dark" (objects darker than background) | "bright"
    "smooth_px": 2.0,              # threshold method: Gaussian sigma
    "split_touching": True,        # threshold method: watershed on the distance transform
    "min_diameter_um": None, "max_diameter_um": None,
    "min_diameter_px": 8, "max_diameter_px": None,
    "min_circularity": 0.0,
    "exclude_border": False,       # mark (not delete) objects touching the image edge as excluded
    "auto_wells": True,            # find bright round wells and keep only objects inside them
    "min_rel_contrast": 0.45,      # threshold method: drop objects fainter than this x the darkest object (shadows)
}

_model_cache: dict = {}
_model_lock = threading.Lock()


def cellpose_info() -> dict:
    try:
        import importlib.metadata as md
        ver = md.version("cellpose")
    except Exception:
        try:
            import cellpose  # noqa: F401
            ver = "unknown"
        except Exception:
            return {"available": False, "version": None, "gpu": False}
    gpu = False
    try:
        import torch
        gpu = bool(torch.cuda.is_available() or (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()))
    except Exception:
        pass
    return {"available": True, "version": ver, "gpu": gpu, "major": _major(ver)}


def _major(ver: str | None) -> int:
    try:
        return int(str(ver).split(".")[0])
    except Exception:
        return 0


def _get_model(model_name: str, use_gpu: bool):
    from cellpose import models
    info = cellpose_info()
    major = info.get("major", 0)
    key = (model_name, use_gpu)
    with _model_lock:
        if key in _model_cache:
            return _model_cache[key]
        gpu = bool(use_gpu and info.get("gpu"))
        import os
        is_path = bool(model_name) and (os.path.exists(model_name) or os.sep in model_name or "/" in model_name)
        if is_path:
            m = models.CellposeModel(gpu=gpu, pretrained_model=model_name)
            kind = "custom"
        elif major >= 4:
            m = models.CellposeModel(gpu=gpu) if model_name in ("auto", "cpsam") else \
                models.CellposeModel(gpu=gpu, pretrained_model=model_name)
            kind = "cpsam"
        else:
            name = {"auto": "cyto3" if major >= 3 else "cyto2"}.get(model_name, model_name)
            # models.Cellpose includes the size model, so the diameter can be estimated automatically
            m = models.Cellpose(gpu=gpu, model_type=name) if hasattr(models, "Cellpose") else \
                models.CellposeModel(gpu=gpu, model_type=name)
            kind = name
        _model_cache[key] = (m, kind, gpu)
        return _model_cache[key]


def _call_eval(model, img, **kw):
    sig = inspect.signature(model.eval)
    accepts_any = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    use = {k: v for k, v in kw.items() if accepts_any or k in sig.parameters}
    out = model.eval(img, **use)
    masks = out[0]
    est_diam = out[3] if len(out) > 3 else None
    return np.asarray(masks), est_diam


def run_cellpose(gray: np.ndarray, s: dict, um_per_px: float | None, log: list[str]) -> np.ndarray:
    info = cellpose_info()
    if not info["available"]:
        raise RuntimeError("Cellpose is not installed in this Python environment (pip install cellpose). "
                           "Use the 'Threshold' method instead, or start the program from your Cellpose environment.")
    img = gray.astype(np.float32)
    if s.get("invert"):
        img = 1.0 - img
    h, w = img.shape
    scale = 1.0
    ms = s.get("max_side") or 0
    if ms and max(h, w) > ms:
        scale = ms / max(h, w)
        img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        log.append(f"Image downscaled x{scale:.3f} for detection ({w}x{h} -> {img.shape[1]}x{img.shape[0]}).")
    diam = None
    if s.get("diameter_um") and um_per_px:
        diam = float(s["diameter_um"]) / um_per_px * scale
    elif s.get("diameter_px"):
        diam = float(s["diameter_px"]) * scale
    model, kind, gpu = _get_model(s.get("model") or "auto", bool(s.get("use_gpu", True)))
    kw = dict(diameter=diam or None,
              flow_threshold=float(s.get("flow_threshold", 0.4)),
              cellprob_threshold=float(s.get("cellprob_threshold", 0.0)),
              max_size_fraction=0.95)  # Cellpose 4 drops masks > 40% of the image by default; organoids can be big
    if info.get("major", 0) < 4:
        kw["channels"] = [0, 0]
    masks, est = _call_eval(model, img, **kw)
    log.append(f"Cellpose {info['version']} model={kind} gpu={gpu} diameter="
               f"{'auto' if not diam else f'{diam:.1f}px'}"
               + (f" (estimated {float(np.mean(est)):.1f}px)" if est is not None and not diam else "")
               + f" flow={kw['flow_threshold']} cellprob={kw['cellprob_threshold']}")
    masks = masks.astype(np.int32)
    if scale != 1.0:
        masks = cv2.resize(masks.astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST).astype(np.int32)
    return masks


def run_threshold(gray: np.ndarray, s: dict, um_per_px: float | None, log: list[str]) -> np.ndarray:
    """Classical method: objects = pixels that differ clearly from the local background.

    The background is a large median filter (wider than an organoid), so uneven illumination, well rims and black
    borders around a well cancel out. The threshold is Otsu on the difference image, then holes are filled and
    touching objects optionally split by watershed."""
    g = gray.astype(np.float32)
    if s.get("invert"):
        g = 1.0 - g
    sig = float(s.get("smooth_px") or 0)
    if sig > 0:
        g = cv2.GaussianBlur(g, (0, 0), sig)
    h, w = g.shape
    # background on a downscaled copy (fast), filter ~ half the short side or 3x the expected diameter
    k = min(1.0, 192 / min(h, w))
    small = cv2.resize(g, (max(8, round(w * k)), max(8, round(h * k))), interpolation=cv2.INTER_AREA)
    d_exp = None
    if s.get("diameter_px"):
        d_exp = float(s["diameter_px"])
    elif s.get("diameter_um") and um_per_px:
        d_exp = float(s["diameter_um"]) / um_per_px
    win = int(3 * d_exp * k) if d_exp else int(min(small.shape) / 2)
    win = max(5, min(win, min(small.shape) - 1)) | 1
    pad = win // 2
    padded = cv2.copyMakeBorder((np.clip(small, 0, 1) * 255).astype(np.uint8), pad, pad, pad, pad, cv2.BORDER_REFLECT)
    bg_small = cv2.medianBlur(padded, win)[pad:-pad, pad:-pad].astype(np.float32) / 255.0
    bg = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)
    wells = detect_wells(gray) if s.get("auto_wells") else None
    if wells is not None:
        # radial background per well: rim darkening, meniscus shadow and vignetting are ring-shaped, organoids
        # are not, so the median intensity at each distance from the well centre is the background there
        wl, nw = ndi.label(wells)
        bg = g.copy()
        for i, sl in enumerate(ndi.find_objects(wl), start=1):
            bg[sl] = _radial_background(g[sl], wl[sl] == i, bg[sl])
        log.append(f"{nw} well(s) found; detecting inside them only (radial background).")
    diff = g - bg
    pol = s.get("polarity", "auto")
    if pol == "auto":
        pol = "dark" if np.percentile(-diff, 99.5) >= np.percentile(diff, 99.5) else "bright"
    dev = -diff if pol == "dark" else diff
    dev = np.clip(dev, 0, None)
    u8 = cv2.normalize(dev, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    t, _ = cv2.threshold(u8[u8 > 0] if (u8 > 0).any() else u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    noise = 1.4826 * np.median(np.abs(diff - np.median(diff)))  # robust background noise
    t_abs = max(t / 255.0 * float(dev.max()), 0.04)
    fg = dev > t_abs
    if wells is not None:
        fg &= wells
    fg = ndi.binary_opening(fg, iterations=1)
    fg = ndi.binary_fill_holes(fg)
    log.append(f"Threshold: background median {win / k:.0f}px, polarity={pol}, cut={t_abs:.3f} "
               f"(noise {noise:.3f}), smoothing sigma={sig}px")
    lab, n = ndi.label(fg)
    rel = float(s.get("min_rel_contrast") or 0)
    if rel > 0 and n > 1:
        # soft shadows (meniscus, illumination) are much fainter than organoids in the same image
        con = np.asarray(ndi.median(dev, lab, index=np.arange(1, n + 1)))
        faint = np.nonzero(con < rel * con.max())[0] + 1
        if len(faint):
            fg[np.isin(lab, faint)] = False
            lab, n = ndi.label(fg)
            log.append(f"{len(faint)} faint object(s) dropped (< {rel:g} x the strongest contrast).")
    if s.get("split_touching", True) and n:
        lab = _watershed_split(fg, lab)
    return lab.astype(np.int32)


def _watershed_split(fg: np.ndarray, lab: np.ndarray) -> np.ndarray:
    """Split touching round objects: distance-transform peaks are the sphere centres, pixels are shared out by a
    power diagram (see edit.power_split)."""
    from .edit import _peaks, power_split
    out = np.zeros_like(lab)
    nid = 1
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        m = lab[sl] == i
        dist = ndi.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
        seeds = _peaks(dist, 8) if m.sum() > 50 else []
        region = out[sl]
        if len(seeds) < 2:
            region[m] = nid
            nid += 1
            continue
        parts = power_split(m, seeds, [float(dist[y, x]) for y, x in seeds])
        for k in range(1, int(parts.max()) + 1):
            pk = parts == k
            if pk.any():
                region[pk] = nid
                nid += 1
    return out


def _radial_background(g: np.ndarray, m: np.ndarray, out: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(m)
    cy, cx = ys.mean(), xs.mean()
    R = np.sqrt(m.sum() / np.pi)
    r = np.hypot(ys - cy, xs - cx)
    step = max(1.0, R / 120)
    b = (r / step).astype(np.int64)
    nb = int(b.max()) + 1
    vals = g[ys, xs]
    order = np.lexsort((vals, b))
    bs, vs = b[order], vals[order]
    starts = np.searchsorted(bs, np.arange(nb))
    ends = np.searchsorted(bs, np.arange(nb), side="right")
    prof = np.array([vs[(s0 + e0) // 2] if e0 > s0 else np.nan for s0, e0 in zip(starts, ends)])
    # rings that are mostly "dark" (an organoid covering the centre) carry no background information:
    # inside half the radius, replace them by the nearest reliable ring further out
    u8 = (np.clip(vals, 0, 1) * 255).astype(np.uint8)
    t, _ = cv2.threshold(u8.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark_frac = np.bincount(b, weights=(u8 < t), minlength=nb) / np.maximum(1, np.bincount(b, minlength=nb))
    bad = (dark_frac > 0.4) & (np.arange(nb) * step < 0.5 * R) | np.isnan(prof)
    good = np.nonzero(~bad)[0]
    if len(good) == 0:
        return np.where(m, np.median(vals), out)
    for j in np.nonzero(bad)[0]:
        prof[j] = prof[good[np.argmin(np.abs(good - j))]] if j * step >= 0.5 * R else prof[good[good > j][0]] \
            if (good > j).any() else prof[good[-1]]
    prof = ndi.median_filter(prof, size=5, mode="nearest")
    res = out.copy()
    res[ys, xs] = prof[b]
    return res


def detect_wells(gray: np.ndarray, erode_frac: float = -0.03) -> np.ndarray | None:
    """Bright, round regions much larger than an organoid (well interiors in brightfield plate images).
    Returns a boolean mask of the (slightly eroded) well interiors, or None if no wells are found."""
    h, w = gray.shape
    k = min(1.0, 1024 / max(h, w))
    g = cv2.resize(gray, (max(8, round(w * k)), max(8, round(h * k))), interpolation=cv2.INTER_AREA) if k < 1 else gray
    g8 = cv2.GaussianBlur((np.clip(g, 0, 1) * 255).astype(np.uint8), (0, 0), 2)
    t, _ = cv2.threshold(g8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = g8 > t
    lab, n = ndi.label(bright)
    if n == 0:
        return None
    total = bright.size
    cands = []
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        m = ndi.binary_fill_holes(lab[sl] == i)
        a = int(m.sum())
        if a < 0.002 * total or a > 0.8 * total:
            continue
        hull = cv2.convexHull(np.argwhere(m)[:, ::-1].astype(np.int32))
        sol = a / max(1.0, cv2.contourArea(hull))
        hh, ww = m.shape
        if sol < 0.9 or not (0.6 < hh / max(ww, 1) < 1.6):
            continue
        cands.append((a, sl, m))
    if not cands:
        return None
    amax = max(c[0] for c in cands)
    mask = np.zeros(g8.shape, bool)
    for a, sl, m in cands:
        if a >= 0.25 * amax:
            # convex hull: organoids sitting on the darker rim of the well make notches in the bright region
            hull = cv2.convexHull(np.argwhere(m)[:, ::-1].astype(np.int32))
            hm = np.zeros(m.shape, np.uint8)
            cv2.fillConvexPoly(hm, hull, 1)
            hm = hm.astype(bool)
            r = int(round(erode_frac * 2 * np.sqrt(a / np.pi)))  # negative = grow (keeps organoids on the rim whole)
            if r > 0:
                hm = ndi.binary_erosion(hm, iterations=r)
            if r < 0:
                big = np.pad(hm, -r)
                big = ndi.binary_dilation(big, iterations=-r)
                y0, x0 = sl[0].start + r, sl[1].start + r
                ys0, xs0 = max(0, y0), max(0, x0)
                ys1, xs1 = min(mask.shape[0], y0 + big.shape[0]), min(mask.shape[1], x0 + big.shape[1])
                mask[ys0:ys1, xs0:xs1] |= big[ys0 - y0:ys1 - y0, xs0 - x0:xs1 - x0]
            else:
                mask[sl] |= hm
    if not mask.any():
        return None
    if k < 1:
        mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
    return mask


def relabel_sequential(lab: np.ndarray) -> np.ndarray:
    u = np.unique(lab)
    u = u[u > 0]
    lut = np.zeros(int(lab.max()) + 1, np.int32)
    lut[u] = np.arange(1, len(u) + 1, dtype=np.int32)
    return lut[lab]


def fill_holes_per_label(lab: np.ndarray) -> np.ndarray:
    out = lab.copy()
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        sub = lab[sl] == i
        filled = ndi.binary_fill_holes(sub)
        region = out[sl]
        region[filled & (region == 0)] = i
    return out


def segment(gray: np.ndarray, settings: dict, um_per_px: float | None = None, roi: dict | None = None):
    """Run detection. Returns (labels, excluded{label: reason}, log)."""
    s = {**DEFAULTS, **(settings or {})}
    log: list[str] = []
    if s["method"] == "cellpose":
        lab = run_cellpose(gray, s, um_per_px, log)
    else:
        lab = run_threshold(gray, s, um_per_px, log)
    lab = fill_holes_per_label(lab)
    if s.get("auto_wells") and s["method"] == "cellpose":
        wells = detect_wells(gray)
        if wells is not None:
            ids = np.unique(lab[wells])
            keep = np.zeros(int(lab.max()) + 1, bool)
            keep[ids] = True
            keep[0] = False
            # keep objects whose centroid lies in a well
            for i, sl in enumerate(ndi.find_objects(lab), start=1):
                if sl is None or not keep[i]:
                    continue
                ys, xs = np.nonzero(lab[sl] == i)
                keep[i] = wells[int(ys.mean()) + sl[0].start, int(xs.mean()) + sl[1].start]
            n0 = int(lab.max())
            lab = np.where(keep[lab], lab, 0).astype(np.int32)
            log.append(f"Wells: {ndi.label(wells)[1]} found; {n0 - int(keep[1:].sum())} detections outside removed.")
    lab, excluded = apply_filters(lab, s, um_per_px, roi, log)
    return lab, excluded, log


def roi_mask(shape, roi: dict | None):
    if not roi or not roi.get("points"):
        return None
    m = np.zeros(shape, np.uint8)
    pts = np.round(np.asarray(roi["points"], np.float64)).astype(np.int32)
    cv2.fillPoly(m, [pts], 1)
    return m.astype(bool)


def apply_filters(lab: np.ndarray, s: dict, um_per_px: float | None, roi: dict | None, log: list[str]):
    """Delete objects outside the ROI / size range / circularity; mark border objects as excluded."""
    from .measure import object_shape

    rm = roi_mask(lab.shape, roi)
    mind = s.get("min_diameter_px") or 0
    maxd = s.get("max_diameter_px") or 0
    if um_per_px:
        if s.get("min_diameter_um"):
            mind = float(s["min_diameter_um"]) / um_per_px
        if s.get("max_diameter_um"):
            maxd = float(s["max_diameter_um"]) / um_per_px
    minc = float(s.get("min_circularity") or 0)
    out = lab.copy()
    removed = {"roi": 0, "small": 0, "large": 0, "circ": 0}
    excluded = {}
    h, w = lab.shape
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        m = lab[sl] == i
        area = int(m.sum())
        d = 2 * np.sqrt(area / np.pi)
        ys, xs = np.nonzero(m)
        cy, cx = ys.mean() + sl[0].start, xs.mean() + sl[1].start
        reason = None
        if rm is not None and not rm[int(round(cy)), int(round(cx))]:
            reason = "roi"
        elif mind and d < mind:
            reason = "small"
        elif maxd and d > maxd:
            reason = "large"
        elif minc > 0:
            sh = object_shape(m)
            if sh and sh["circularity"] < minc:
                reason = "circ"
        if reason:
            out[sl][m] = 0
            removed[reason] += 1
            continue
        if s.get("exclude_border") and (sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == h or sl[1].stop == w):
            excluded[i] = "touches image edge"
    out = relabel_sequential(out)
    remap = {}  # excluded ids after relabelling
    if excluded:
        for old in list(excluded):
            vals = out[lab == old]
            if vals.size and vals[0] > 0:
                remap[int(vals[0])] = excluded[old]
    txt = ", ".join(f"{v} {k}" for k, v in removed.items() if v)
    log.append(f"{int(out.max())} objects kept" + (f"; removed: {txt}" if txt else "")
               + (f"; {len(remap)} touching the edge marked excluded" if remap else ""))
    return out, remap
