"""Per-organoid shape measurements and outline extraction."""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy import ndimage as ndi

FUSED_SOLIDITY = 0.90  # below this (or elongated + below 0.97) an object is flagged "possibly fused / irregular" (a hint)


def _contour(mask: np.ndarray, offset=(0, 0)):
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)
    return c + np.array([[offset[1], offset[0]]], dtype=c.dtype)


def _min_caliper(hull: np.ndarray) -> float:
    """Minimum Feret diameter (rotating calipers over hull edges)."""
    pts = hull.reshape(-1, 2).astype(np.float64)
    n = len(pts)
    if n < 3:
        return 0.0
    best = math.inf
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        e = q - p
        L = math.hypot(*e)
        if L == 0:
            continue
        nrm = np.array([-e[1], e[0]]) / L
        d = (pts - p) @ nrm
        best = min(best, d.max() - d.min())
    return float(best)


def object_shape(mask: np.ndarray, offset=(0, 0)) -> dict | None:
    c = _contour(mask, offset)
    if c is None:
        return None
    area = float(mask.sum())
    per = float(cv2.arcLength(c, True))
    hull = cv2.convexHull(c)
    hull_area = float(cv2.contourArea(hull)) or area
    hp = hull.reshape(-1, 2).astype(np.float64)
    if len(hp) >= 2:
        d = np.sqrt(((hp[:, None, :] - hp[None, :, :]) ** 2).sum(-1))
        feret = float(d.max()) + 1.0  # +1: pixel extent, not centre-to-centre
    else:
        feret = 1.0
    feret_min = _min_caliper(hull) + 1.0
    if len(c) >= 5:
        (_, _), (a1, a2), ang = cv2.fitEllipse(c)
        major, minor = max(a1, a2), min(a1, a2)
    else:
        major = minor = 2 * math.sqrt(area / math.pi)
        ang = 0.0
    circ = 4 * math.pi * area / (per * per) if per > 0 else 0.0
    return {
        "area": area, "perimeter": per, "eq_diam": 2 * math.sqrt(area / math.pi),
        "feret_max": feret, "feret_min": feret_min, "major": float(major), "minor": float(minor),
        "angle": float(ang), "circularity": min(1.0, circ), "solidity": min(1.0, area / hull_area),
        "contour": c,
    }


def simplify(c: np.ndarray, eps: float = 0.6) -> list:
    s = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
    return s.tolist()


def reading_order(objs: list[dict]) -> list[dict]:
    """Sort objects top-to-bottom in rows, left-to-right inside a row (rows ~ one median diameter tall)."""
    if not objs:
        return objs
    md = float(np.median([o["eq_diam_px"] for o in objs])) or 1.0
    return sorted(objs, key=lambda o: (round(o["cy"] / md), o["cx"]))


def measure_all(labels: np.ndarray, gray: np.ndarray | None, um_per_px: float | None, meta: dict) -> list[dict]:
    """meta: {label: {"status": "included"|"excluded", "reason": str, "source": str}}"""
    h, w = labels.shape
    out = []
    for i, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        m = labels[sl] == i
        if not m.any():
            continue
        # pad so contours on the crop edge are closed correctly
        mp = np.pad(m, 1)
        sh = object_shape(mp, offset=(sl[0].start - 1, sl[1].start - 1))
        if sh is None:
            continue
        ys, xs = np.nonzero(m)
        cy = float(ys.mean() + sl[0].start)
        cx = float(xs.mean() + sl[1].start)
        mi = float(gray[sl][m].mean()) if gray is not None else None
        md = meta.get(i, {})
        border = sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == h or sl[1].stop == w
        flags = []
        ar = sh["major"] / sh["minor"] if sh["minor"] > 0 else 1.0
        if sh["solidity"] < FUSED_SOLIDITY or (ar > 1.5 and sh["solidity"] < 0.97):
            flags.append("possibly fused / irregular")
        if border:
            flags.append("touches image edge")
        o = {
            "label": i, "cx": cx, "cy": cy,
            "bbox": [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop],
            "area_px": sh["area"], "perimeter_px": sh["perimeter"], "eq_diam_px": sh["eq_diam"],
            "feret_max_px": sh["feret_max"], "feret_min_px": sh["feret_min"],
            "major_px": sh["major"], "minor_px": sh["minor"], "angle_deg": sh["angle"],
            "circularity": sh["circularity"], "solidity": sh["solidity"],
            "aspect_ratio": sh["major"] / sh["minor"] if sh["minor"] > 0 else None,
            "mean_intensity": mi, "touches_border": border,
            "status": md.get("status", "included"), "reason": md.get("reason", ""),
            "source": md.get("source", ""), "flags": flags,
            "contour": simplify(sh["contour"]),
        }
        if um_per_px:
            u = um_per_px
            o.update({
                "area_um2": sh["area"] * u * u, "eq_diam_um": sh["eq_diam"] * u,
                "feret_max_um": sh["feret_max"] * u, "feret_min_um": sh["feret_min"] * u,
                "major_um": sh["major"] * u, "minor_um": sh["minor"] * u, "perimeter_um": sh["perimeter"] * u,
            })
        out.append(o)
    objs = reading_order(out)
    for n, o in enumerate(objs, start=1):
        o["number"] = n
    return objs


CSV_COLUMNS = [
    ("image", "image"), ("organoid_number", "number"), ("status", "status"), ("exclusion_reason", "reason"),
    ("flags", "flags"), ("source", "source"),
    ("centroid_x_px", "cx"), ("centroid_y_px", "cy"),
    ("area_px", "area_px"), ("area_um2", "area_um2"), ("area_mm2", "area_mm2"),
    ("equivalent_diameter_px", "eq_diam_px"), ("equivalent_diameter_um", "eq_diam_um"),
    ("feret_max_um", "feret_max_um"), ("feret_min_um", "feret_min_um"),
    ("ellipse_major_um", "major_um"), ("ellipse_minor_um", "minor_um"),
    ("perimeter_um", "perimeter_um"), ("aspect_ratio", "aspect_ratio"),
    ("circularity", "circularity"), ("solidity", "solidity"),
    ("volume_sphere_mm3", "vol_sphere_mm3"), ("volume_ellipsoid_mm3", "vol_ellipsoid_mm3"),
    ("mean_intensity_0to1", "mean_intensity"), ("touches_image_edge", "touches_border"),
    ("um_per_px", "um_per_px"), ("calibration_source", "cal_source"),
]


def csv_row(o: dict, image: str, cal: dict) -> dict:
    u = cal.get("um_per_px")
    r = dict(o)
    r["image"] = image
    r["flags"] = "; ".join(o.get("flags", []))
    r["um_per_px"] = u
    r["cal_source"] = cal.get("source", "none") if u else "none (pixels only)"
    if u:
        r["area_mm2"] = o["area_um2"] / 1e6
        d_mm = o["eq_diam_um"] / 1000
        r["vol_sphere_mm3"] = math.pi / 6 * d_mm ** 3
        a, b = o["major_um"] / 2000, o["minor_um"] / 2000  # semi-axes in mm; prolate spheroid (b = c)
        r["vol_ellipsoid_mm3"] = 4 / 3 * math.pi * a * b * b
    return {col: _fmt(r.get(key)) for col, key in CSV_COLUMNS}


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.6g}"
    return v
